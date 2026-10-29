	if err = validateSubnetGroupName(desired.ko.Spec.Name); err != nil {
		return nil, err
	}
