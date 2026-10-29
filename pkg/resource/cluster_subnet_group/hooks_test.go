// Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License"). You may
// not use this file except in compliance with the License. A copy of the
// License is located at
//
//     http://aws.amazon.com/apache2.0/
//
// or in the "license" file accompanying this file. This file is distributed
// on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
// express or implied. See the License for the specific language governing
// permissions and limitations under the License.

package cluster_subnet_group

import (
	"context"
	"errors"
	"strings"
	"testing"

	ackerr "github.com/aws-controllers-k8s/runtime/pkg/errors"
	"github.com/aws/aws-sdk-go-v2/aws"

	svcapitypes "github.com/aws-controllers-k8s/redshift-controller/apis/v1alpha1"
)

func TestHasUpperCase(t *testing.T) {
	for _, tc := range []struct {
		name *string
		want bool
	}{
		{nil, false},
		{aws.String(""), false},
		{aws.String("abc-1"), false},
		{aws.String("Abc"), true},
		{aws.String("ABC"), true},
		{aws.String("abc-D"), true},
	} {
		if got := hasUpperCase(tc.name); got != tc.want {
			t.Errorf("hasUpperCase(%q) = %v, want %v", aws.ToString(tc.name), got, tc.want)
		}
	}
}

func TestValidateSubnetGroupName(t *testing.T) {
	for _, name := range []*string{nil, aws.String("abc-1")} {
		if err := validateSubnetGroupName(name); err != nil {
			t.Errorf("validateSubnetGroupName(%q) = %v, want nil", aws.ToString(name), err)
		}
	}
	err := validateSubnetGroupName(aws.String("My-Group"))
	var terminal *ackerr.TerminalError
	if !errors.As(err, &terminal) {
		t.Fatalf("got %v, want a *ackerr.TerminalError", err)
	}
	for _, want := range []string{"spec.name is immutable", `"my-group"`} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error %q does not contain %q", err.Error(), want)
		}
	}
}

// TestUpperCaseNameGuards exercises the generated wiring of both hooks: each
// must return before any AWS call, so a zero-value resourceManager suffices.
func TestUpperCaseNameGuards(t *testing.T) {
	res := func(name string) *resource {
		return &resource{&svcapitypes.ClusterSubnetGroup{
			Spec: svcapitypes.ClusterSubnetGroupSpec{Name: aws.String(name)},
		}}
	}
	rm := &resourceManager{}
	ctx := context.Background()

	if _, err := rm.sdkFind(ctx, res("Foo")); err != ackerr.NotFound {
		t.Errorf("sdkFind = %v, want ackerr.NotFound", err)
	}
	_, err := rm.sdkCreate(ctx, res("Foo"))
	var terminal *ackerr.TerminalError
	if !errors.As(err, &terminal) {
		t.Errorf("sdkCreate = %v, want a *ackerr.TerminalError", err)
	}
}
