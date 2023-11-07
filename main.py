import json
import boto3
import os


class oc_cluster:
    def __init__(self, cluster_detail):
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
def get_cluster_list():
    run_command('ocm list clusters > clusters.txt')
def run_command(command):
    output = os.popen(command).read()
    print(output)
    return output

def hibernate_cluster(cluster_name):
    commmand = f'ocm hibernate cluster {cluster_name}'
    run_command(commmand)
    print(f'Hibernated {cluster_name}')

def resume_cluster(cluster_name):
    commmand = f'ocm hibernate cluster {cluster_name}'
    run_command(commmand)
    print(f'Hibernated {cluster_name}')
def main():
    ec2_map = json.load(open('ec2.json'))
    ec2_map = [ec2 for ec2 in ec2_map['Reservations'] if ec2['Instances'][0]['State']['Name'] == 'running']
    ec2_map = [instance for ec2 in ec2_map for instance in ec2['Instances']]

    ec2_map = {list(filter(lambda obj: obj['Key'] == 'Name', instance['Tags']))[0]['Value']:instance for instance in ec2_map}
    ec2_names = list(ec2_map.keys())
    print(len(ec2_map))
    clusters = []
    get_cluster_list()
    clusters_details = open('clusters.txt').readlines()
    for cluster_detail in clusters_details:
        clusters.append(oc_cluster(cluster_detail))
    clusters = [cluster for cluster in clusters if cluster.cloud_provider == 'aws']
    print(len(clusters))
    print([cluster.name for cluster in clusters])
    osd_rosa_ec2 = []
    for cluster in clusters:
        cluster.nodes += [ec2_name for ec2_name in ec2_names if ec2_name.startswith(f'{cluster.name}-')]
        ec2_names = list(set(ec2_names) - set(cluster.nodes))
        [ec2_map.pop(ec2_name)  for ec2_name in cluster.nodes]

    print(len(ec2_map))
    print(ec2_names)
    ec2_names = [ec2_name for ec2_name in ec2_names if ec2_map[ec2_name]]
    for ec2_name in ec2_names:
        tags = ec2_map[ec2_name]['Tags']
        tags = {tag['Key']:tag['Value'] for tag in tags}
        if 'red-hat-clustertype' in tags and (tags['red-hat-clustertype'] == 'osd' or tags['red-hat-clustertype'] == 'rosa'):
            osd_rosa_ec2.append(ec2_name)

    print(osd_rosa_ec2)
    print(len(osd_rosa_ec2))
    osd_clusters = [cluster for cluster in clusters if cluster.type == 'osd']
    osd_names = sorted([cluster.name for cluster in osd_clusters])
    for cluster in osd_names:
        print(cluster)
    # iam = boto3.client('iam')
    # username = iam.get_user()["User"]["UserName"]
    # print(username)

if __name__ == '__main__':
    main()