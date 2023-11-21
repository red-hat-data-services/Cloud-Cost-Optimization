import boto3

def get_instances_for_region(region, current_state):
    ec2_client = boto3.client('ec2', region_name=region)
    filters = [{'Name': 'instance-state-name', 'Values': [current_state]}]
    ec2_map = ec2_client.describe_instances(Filters=filters, MaxResults=1000)
    ec2_map = [ec2 for ec2 in ec2_map['Reservations']]
    ec2_map = [instance for ec2 in ec2_map for instance in ec2['Instances']]
    ec2_map = {list(filter(lambda obj: obj['Key'] == 'Name', instance['Tags']))[0]['Value']: instance for instance in
               ec2_map}
    print(region, len(ec2_map))
    return ec2_map

def get_all_instances(ec2_instances, current_state):
    client = boto3.client('ec2', region_name='us-east-1')
    regions = [region['RegionName'] for region in client.describe_regions()['Regions']]
    for region in regions:
        ec2_instances[region] = get_instances_for_region(region, current_state)

def get_all_ebs_volumes(volumes, current_state):
    client = boto3.client('ec2', region_name='us-east-1')
    regions = [region['RegionName'] for region in client.describe_regions()['Regions']]
    for region in regions:
        volumes[region] = get_ebs_volume_for_region(region, current_state)

def get_ebs_volume_for_region(region, current_state):
    ec2_client = boto3.client('ec2', region_name=region)
    filters = [{'Name': 'status', 'Values': [current_state]}]
    volume_map = ec2_client.describe_volumes(Filters=filters, MaxResults=500)
    volume_map = [volume for volume in volume_map['Volumes']]
    volume_map = {volume['VolumeId']: volume for volume in
               volume_map}
    print(region, len(volume_map))
    return volume_map

def cleanup_available_volumes(volumes:dict):
    for region, ebs_volumes in volumes.items():
        ec2_client = boto3.client('ec2', region_name=region)
        print(f'starting to clean volumes for region {region}')
        for volumeId in ebs_volumes:
            print(f'Deleting volume {volumeId}')
            ec2_client.delete_volume(VolumeId=volumeId)

def main():
    volumes = {}
    get_all_ebs_volumes(volumes, 'available')
    cleanup_available_volumes(volumes)


if __name__ == '__main__':
    main()