# Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may
# not use this file except in compliance with the License. A copy of the
# License is located at
#
#         http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is distributed
# on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.

"""Integration tests for the Redshift ClusterSubnetGroup resource."""

import time
import pytest
import logging

from acktest.k8s import condition
from acktest.k8s import resource as k8s
from acktest.resources import random_suffix_name
from e2e import service_marker, CRD_GROUP, CRD_VERSION, load_redshift_resource
from e2e.replacement_values import REPLACEMENT_VALUES

RESOURCE_PLURAL = "clustersubnetgroups"

DELETE_WAIT_AFTER_SECONDS = 10

# Creating a resource with tags requires a secondary sync, which the
# controller requeues after 30 seconds.
SYNC_WAIT_PERIODS = 12
SYNC_PERIOD_LENGTH = 5


@pytest.fixture(scope="module")
def redshift_client():
    import boto3
    return boto3.client("redshift")


@pytest.fixture(scope="module")
def ec2_client():
    import boto3
    return boto3.client("ec2")


def get_subnet_ids(ec2_client):
    """Get two subnet IDs from the default VPC for testing."""
    resp = ec2_client.describe_subnets(
        Filters=[{"Name": "default-for-az", "Values": ["true"]}],
    )
    subnets = resp.get("Subnets", [])
    assert len(subnets) > 1, "At least two default subnets are needed for testing"
    return subnets[0]["SubnetId"], subnets[1]["SubnetId"]


def patch_and_wait_for_sync(ref, updates):
    """Patch the resource and wait for a reconcile that happened after the patch.

    ACK.ResourceSynced is usually still True from the previous reconcile right
    after a patch, so only accept a condition with a newer lastTransitionTime.
    """
    last_synced = condition.get_synced_last_transition_time(ref)
    k8s.patch_custom_resource(ref, updates)
    assert k8s.wait_on_condition_after(
        ref,
        condition.CONDITION_TYPE_RESOURCE_SYNCED,
        "True",
        last_transition_after=last_synced,
        wait_periods=SYNC_WAIT_PERIODS,
        period_length=SYNC_PERIOD_LENGTH,
    )


def describe_subnet_group(redshift_client, name):
    resp = redshift_client.describe_cluster_subnet_groups(
        ClusterSubnetGroupName=name,
    )
    groups = resp["ClusterSubnetGroups"]
    assert len(groups) == 1
    return groups[0]


def aws_tags(group):
    return {t["Key"]: t["Value"] for t in group.get("Tags", [])}


@service_marker
@pytest.mark.canary
class TestClusterSubnetGroup:
    def test_create_update_delete(self, redshift_client, ec2_client):
        subnet_id, second_subnet_id = get_subnet_ids(ec2_client)
        resource_name = random_suffix_name("ack-test-csg", 24)
        description = "ACK test cluster subnet group"

        replacements = REPLACEMENT_VALUES.copy()
        replacements["CLUSTER_SUBNET_GROUP_NAME"] = resource_name
        replacements["DESCRIPTION"] = description
        replacements["SUBNET_ID_1"] = subnet_id

        resource_data = load_redshift_resource(
            "cluster_subnet_group",
            additional_replacements=replacements,
        )
        logging.info(f"Creating ClusterSubnetGroup {resource_name}")

        # Create the resource
        ref = k8s.CustomResourceReference(
            CRD_GROUP, CRD_VERSION, RESOURCE_PLURAL,
            resource_name, namespace="default",
        )
        k8s.create_custom_resource(ref, resource_data)
        cr = k8s.wait_resource_consumed_by_controller(ref)

        assert cr is not None
        assert k8s.get_resource_exists(ref)

        assert k8s.wait_on_condition(
            ref,
            condition.CONDITION_TYPE_RESOURCE_SYNCED,
            "True",
            wait_periods=SYNC_WAIT_PERIODS,
            period_length=SYNC_PERIOD_LENGTH,
        )

        # Verify the resource exists in AWS
        cr = k8s.get_resource(ref)
        group = describe_subnet_group(redshift_client, resource_name)
        assert group["ClusterSubnetGroupName"] == resource_name
        assert group["Description"] == description

        # Verify status fields are populated
        assert cr["status"].get("subnetGroupStatus") is not None
        assert cr["status"].get("vpcID") is not None
        assert cr["status"].get("subnets") is not None
        assert len(cr["status"]["subnets"]) > 0

        # Verify tags in AWS
        assert aws_tags(group).get("environment") == "test"

        # --- Update: change description ---
        new_description = "Updated ACK test cluster subnet group"
        patch_and_wait_for_sync(ref, {"spec": {"description": new_description}})

        group = describe_subnet_group(redshift_client, resource_name)
        assert group["Description"] == new_description

        # --- Update: change tags ---
        patch_and_wait_for_sync(ref, {
            "spec": {
                "tags": [
                    {"key": "environment", "value": "staging"},
                    {"key": "team", "value": "platform"},
                ],
            },
        })

        tags = aws_tags(describe_subnet_group(redshift_client, resource_name))
        assert tags.get("environment") == "staging"
        assert tags.get("team") == "platform"

        # --- Update: remove a tag ---
        patch_and_wait_for_sync(ref, {
            "spec": {
                "tags": [
                    {"key": "team", "value": "platform"},
                ],
            },
        })

        tags = aws_tags(describe_subnet_group(redshift_client, resource_name))
        assert "environment" not in tags
        assert tags.get("team") == "platform"

        # --- Update: add a subnet ---
        patch_and_wait_for_sync(ref, {
            "spec": {
                "subnetIDs": [subnet_id, second_subnet_id],
            },
        })

        # Verify subnet update in AWS and in the CR
        group = describe_subnet_group(redshift_client, resource_name)
        aws_subnet_ids = {s["SubnetIdentifier"] for s in group["Subnets"]}
        assert aws_subnet_ids == {subnet_id, second_subnet_id}
        cr = k8s.get_resource(ref)
        assert set(cr["spec"]["subnetIDs"]) == {subnet_id, second_subnet_id}

        # --- Delete ---
        k8s.delete_custom_resource(ref)
        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Verify resource is deleted from Kubernetes
        assert not k8s.get_resource_exists(ref)

        # Verify resource is deleted from AWS
        try:
            redshift_client.describe_cluster_subnet_groups(
                ClusterSubnetGroupName=resource_name,
            )
            assert False, "Expected ClusterSubnetGroupNotFoundFault"
        except redshift_client.exceptions.ClusterSubnetGroupNotFoundFault:
            pass
