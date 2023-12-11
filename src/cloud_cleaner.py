import json

import boto3, os

class oc_cluster:
    def __init__(self, cluster_detail, ocm_account):
        details = cluster_detail.split(' ')
        details = [detail for detail in details if detail]
        self.id = details[0]
        self.name = details[1]
        self.api_url = details[2]
        self.ocp_version = details[3]
        self.type = details[4]
        self.hcp = details[5]
        self.cloud_provider = details[6]
        self.region = details[7]
        self.status = details[8]
        self.nodes = []
        self.ocm_account = ocm_account

def get_all_cluster_details(ocm_account:str, clusters:list):
    get_cluster_list(ocm_account)
    clusters_details = open(f'clusters_{ocm_account}.txt').readlines()
    for cluster_detail in clusters_details:
        clusters.append(oc_cluster(cluster_detail, ocm_account))
    clusters = [cluster for cluster in clusters if cluster.cloud_provider == 'aws']

def get_cluster_list(ocm_account:str):
    run_command(f'script/./get_all_cluster_details.sh {ocm_account}')

def run_command(command):
    output = os.popen(command).read()
    print(output)
    return output

def get_instances_for_region(region, current_state):
    ec2_client = boto3.client('ec2', region_name=region)
    filters = [{'Name': 'instance-state-name', 'Values': [current_state]}]
    ec2_map = ec2_client.describe_instances(Filters=filters, MaxResults=1000)
    ec2_map = [ec2 for ec2 in ec2_map['Reservations']]
    ec2_map = [instance for ec2 in ec2_map for instance in ec2['Instances']]
    # ec2_map = {list(filter(lambda obj: obj['Key'] == 'Name', instance['Tags']))[0]['Value']: instance for instance in
    #            ec2_map}
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
def check_if_given_tag_exists(tag_name, volume):
    result = False
    if 'Tags' in volume:
        tags = volume['Tags']
        print(tags)
        for tag in tags:
            if tag['Key'] == tag_name:
                result = True
                break
    return result
def get_ebs_volume_for_region(region, current_state):
    ec2_client = boto3.client('ec2', region_name=region)
    filters = [{'Name': 'status', 'Values': [current_state]}]
    volume_map = ec2_client.describe_volumes(Filters=filters, MaxResults=500)
    volume_map = [volume for volume in volume_map['Volumes'] if not check_if_given_tag_exists(
                'KubernetesCluster', volume)]
    volume_map = {volume['VolumeId']: volume for volume in
               volume_map}
    print(region, len(volume_map))
    return volume_map

def cleanup_available_volumes(volumes:dict):
    for region, ebs_volumes in volumes.items():
        ec2_client = boto3.client('ec2', region_name=region)
        print(f'starting to clean volumes for region {region}')
        print('volumes to be deleted -', [volumeId for volumeId in ebs_volumes ])
        for volumeId in ebs_volumes:
            print(f'Deleting volume {volumeId}')
            # ec2_client.delete_volume(VolumeId=volumeId)

def get_all_elbs(elbs):
    client = boto3.client('ec2', region_name='us-east-1')
    regions = [region['RegionName'] for region in client.describe_regions()['Regions']]
    for region in regions:
        elbs[region] = get_elbs_for_region(region)

def get_elbs_for_region(region):
    aws_client = boto3.client('elb', region_name=region)
    all_elb_map = {}
    elb_map = aws_client.describe_load_balancers(PageSize=400)
    elb_map = [elb for elb in elb_map['LoadBalancerDescriptions']]
    print(region, len(elb_map))
    all_elb_map['nlb'] = elb_map

    aws_client = boto3.client('elbv2', region_name=region)
    elb_map = aws_client.describe_load_balancers(PageSize=400)
    elb_map = [elb for elb in elb_map['LoadBalancers']]
    print(region, len(elb_map))
    all_elb_map['alb'] = elb_map

    return all_elb_map

def get_target_groups_health(LoadBalancerArn, region):
    aws_client = boto3.client('elbv2', region_name=region)
    target_groups = aws_client.describe_target_groups(LoadBalancerArn=LoadBalancerArn)
    healths = []
    for target_group in target_groups['TargetGroups']:
        targets = aws_client.describe_target_health(TargetGroupArn=target_group['TargetGroupArn'])
        for target in targets['TargetHealthDescriptions']:
            if 'TargetHealth' in target:
                healths.append(target['TargetHealth']['State'])
    return healths

def name_starts_with_existing_cluster(name, cluster_names:list[str]):
    result = False
    for cluster_name in cluster_names:
        if name.startswith(f'{cluster_name}-'):
            result = True
            break
    return result

def elb_belongs_to_existing_cluster( cluster_names:list[str], tags:dict):
    result = False

    if 'Name' in tags:
        result = name_starts_with_existing_cluster(tags['Name'], cluster_names)

    if not result:
        for key, value in tags.items():
            if key.startswith('kubernetes.io/cluster/'):
                possible_cluster_suffix = key.split('/')[-1]
                result = name_starts_with_existing_cluster(possible_cluster_suffix, cluster_names)
                if result:
                    break


    return result

def get_all_tags_for_nlbs(LoadBalancerNames:list, region):
    tags = {}
    aws_client = boto3.client('elb', region_name=region)
    chunk_size = 20
    start, end = 0, chunk_size if len(LoadBalancerNames) > chunk_size else len(LoadBalancerNames)
    while end <= len(LoadBalancerNames):
        elb_tags = aws_client.describe_tags(LoadBalancerNames=LoadBalancerNames[start:end])
        elb_tags = {elb['LoadBalancerName']: elb['Tags'] for elb in elb_tags['TagDescriptions']}
        tags.update(elb_tags)
        start, end = end, end + chunk_size if len(LoadBalancerNames) > end + chunk_size else len(LoadBalancerNames)
        if start >= end:
            break

    return tags


def get_all_tags_for_albs(ResourceArns:list, region):
    tags = {}
    aws_client = boto3.client('elbv2', region_name=region)
    chunk_size = 20
    start, end = 0, chunk_size if len(ResourceArns) > chunk_size else len(ResourceArns)
    while end <= len(ResourceArns):
        elb_tags = aws_client.describe_tags(ResourceArns=ResourceArns[start:end])
        elb_tags = {elb['ResourceArn']: elb['Tags'] for elb in elb_tags['TagDescriptions']}
        tags.update(elb_tags)
        start, end = end, end + chunk_size if len(ResourceArns) > end + chunk_size else len(ResourceArns)
        if start >= end:
            break

    return tags

def cleanup_inactive_elbs(elbs:dict[dict], clusters:dict[oc_cluster]):
    rosa_clusters_ids = [cluster.id for cluster in clusters if cluster.type == 'rosa']
    osd_cluster_names = [cluster.name for cluster in clusters if cluster.type == 'osd']
    all_cluster_names = [cluster.name for cluster in clusters]

    for region, elbs_for_region in elbs.items():
        nlbs_to_be_deleted = []
        elbs_to_be_deleted = []
        print(f'starting to cleanup elbs for region {region}')

        print(f'starting with classic load balancers (nlb) for region {region}')
        aws_client = boto3.client('elb', region_name=region)
        nlb_tags = {}
        if elbs_for_region['nlb']:
            nlb_tags = get_all_tags_for_nlbs([nlb['LoadBalancerName'] for nlb in elbs_for_region['nlb']], region)
        for nlb in elbs_for_region['nlb']:
            if not nlb['Instances'] and not elb_belongs_to_existing_cluster(all_cluster_names, tags):
                # print(f'Cleaning up nlb {nlb["LoadBalancerName"]}')
                # aws_client.delete_load_balancer(LoadBalancerName=nlb['LoadBalancerName'])
                nlbs_to_be_deleted.append(nlb["LoadBalancerName"])
            else:
                a=1
                # print(f'Not cleaning up nlb {nlb["LoadBalancerName"]}, since it has instances attached')

        print(f'starting with application load balancers (alb) for region {region}')
        aws_client = boto3.client('elbv2', region_name=region)
        elb_tags = {}
        if elbs_for_region['alb']:
            elb_tags = get_all_tags_for_albs([alb['LoadBalancerArn'] for alb in elbs_for_region['alb']], region)
        for alb in elbs_for_region['alb']:
            # alb['LoadBalancerArn']
            target_groups_health = get_target_groups_health(alb['LoadBalancerArn'], region)
            if 'healthy' in target_groups_health:
                # print(f'Not cleaning up alb {alb["LoadBalancerArn"]}, since it has healty target groups')
                continue


            tags = elb_tags[alb['LoadBalancerArn']]
            tags = {tag['Key']:tag['Value'] for tag in tags}
            # print(f'starting with alb {alb["LoadBalancerArn"]}')
            if 'red-hat-clustertype' in tags:
                if tags['red-hat-clustertype'] == 'rosa' and tags['api.openshift.com/id'] in rosa_clusters_ids:
                    # print(f'Not cleaning up alb {alb["LoadBalancerArn"]}, since it belongs to the existing rosa cluster {tags["api.openshift.com/id"]}')
                    continue
                elif tags['red-hat-clustertype'] == 'osd' and elb_belongs_to_existing_cluster(osd_cluster_names, tags):
                    # print(f'Not cleaning up alb {alb["LoadBalancerArn"]}, since it belongs to the existing osd cluster {alb["LoadBalancerName"]}')
                    continue
            elif elb_belongs_to_existing_cluster(all_cluster_names, tags):
                # print(f'Not cleaning up alb {alb["LoadBalancerArn"]}, since it belongs to the existing cluster {alb["LoadBalancerName"]}')
                continue

            # print(f'Deleting the ALB {alb["LoadBalancerArn"]}')
            elbs_to_be_deleted.append({alb["LoadBalancerName"] : alb["LoadBalancerArn"]})

        print('nlbs_to_be_deleted', len(nlbs_to_be_deleted), json.dumps(nlbs_to_be_deleted, indent=4))
        print('elbs_to_be_deleted', len(elbs_to_be_deleted), json.dumps(elbs_to_be_deleted, indent=4))




def cleanup_all_netoworking_data(ec2_running_instances:dict, ec2_stopped_instances:dict):
    client = boto3.client('ec2', region_name='us-east-1')
    regions = [region['RegionName'] for region in client.describe_regions()['Regions']]
    associated_vpcs = []
    for region in regions:
        print(f'starting with the region {region}')
        ec2_client = boto3.client('ec2', region_name=region)

        vpc_exceptions = ['vpc-00331e896a900165b'] #shared-rosa-hcp-vpc
        associated_vpcs = list(set([instance['VpcId'] for instance in ec2_running_instances[region]] + [instance['VpcId'] for instance in ec2_stopped_instances[region]] + vpc_exceptions))
        all_vpcs  =  ec2_client.describe_vpcs(MaxResults=500)
        all_vpcs = [vpc['VpcId'] for vpc in all_vpcs['Vpcs']]
        print('vpcs', len(all_vpcs), len(associated_vpcs))

        associated_network_interfaces = list(set([interface['NetworkInterfaceId'] for instance in ec2_running_instances[region] for interface in instance['NetworkInterfaces']] + [interface['NetworkInterfaceId'] for instance in ec2_stopped_instances[region] for interface in instance['NetworkInterfaces']]))
        all_network_interfaces  =  ec2_client.describe_network_interfaces(MaxResults=500)
        all_network_interfaces = [network_interface['NetworkInterfaceId'] for network_interface in all_network_interfaces['NetworkInterfaces']]
        print('network_interfaces', len(all_network_interfaces), len(associated_network_interfaces))

        filters = [{'Name': 'vpc-id', 'Values': associated_vpcs}]
        associated_subnets = ec2_client.describe_subnets(Filters=filters, MaxResults=500)
        associated_subnets = [subnet['SubnetId'] for subnet in associated_subnets['Subnets']]
        all_subnets  =  ec2_client.describe_subnets(MaxResults=500)
        all_subnets = [subnet['SubnetId'] for subnet in all_subnets['Subnets']]
        print('subnets', len(all_subnets), len(associated_subnets))

        nat_gateway_exceptions = ['nat-0fe88f6e5c09c380a']  # shared-rosa-hcp-vpc-use1-az1
        filters = [{'Name': 'vpc-id', 'Values': associated_vpcs}] if associated_vpcs else []
        associated_nat_gateways = ec2_client.describe_nat_gateways(Filters=filters, MaxResults=500)
        associated_nat_gateways = list(set([nat_gateway['NatGatewayId'] for nat_gateway in associated_nat_gateways['NatGateways']] + nat_gateway_exceptions))
        all_nat_gateways  =  ec2_client.describe_nat_gateways(MaxResults=500)
        all_nat_gateways = [nat_gateway['NatGatewayId'] for nat_gateway in all_nat_gateways['NatGateways']]
        print('nat_gateways', len(all_nat_gateways), len(associated_nat_gateways))

        filters = [{'Name': 'network-interface-id', 'Values': associated_network_interfaces}]
        associated_elastic_ip_addresses = ec2_client.describe_addresses(Filters=filters)
        associated_elastic_ip_addresses = [elastic_ip_address['AllocationId'] for elastic_ip_address in associated_elastic_ip_addresses['Addresses']]
        all_elastic_ip_addresses  =  ec2_client.describe_addresses()
        all_elastic_ip_addresses = [elastic_ip_address['AllocationId'] for elastic_ip_address in all_elastic_ip_addresses['Addresses']]
        print('elastic_ip_addresses', len(all_elastic_ip_addresses), len(associated_elastic_ip_addresses))









def main():
    clusters = []
    ocm_accounts = ['PROD', 'STAGE']
    for ocm_account in ocm_accounts:
        get_all_cluster_details(ocm_account, clusters)

    # ec2_running_instances = {}
    # get_all_instances(ec2_running_instances, 'running')
    # ec2_stopped_instances = {}
    # get_all_instances(ec2_stopped_instances, 'stopped')
    volumes = {}
    # get_all_ebs_volumes(volumes, 'available')
    # cleanup_available_volumes(volumes)

    elbs = {}
    get_all_elbs(elbs)
    cleanup_inactive_elbs(elbs, clusters)
    print(elbs)

    # cleanup_all_netoworking_data(ec2_running_instances, ec2_stopped_instances)


if __name__ == '__main__':
    main()