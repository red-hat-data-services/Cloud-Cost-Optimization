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
    print(run_command())
    print(f'Hibernated {cluster_name}')

def resume_cluster(cluster_name):
    commmand = f'ocm hibernate cluster {cluster_name}'
    run_command(commmand)
    print(f'Hibernated {cluster_name}')
def main():
    clusters = []
    get_cluster_list()
    clusters_details = open('clusters.txt').readlines()
    for cluster_detail in clusters_details:
        clusters.append(oc_cluster(cluster_detail))
    clusters = [cluster for cluster in clusters if cluster.cloud_provider == 'aws']
    print(len(clusters))

    clusters_to_hibernate = [cluster for cluster in clusters if (cluster.type == 'osd' or (cluster.type == 'rosa' and cluster.hcp == 'false')) and cluster.status == 'ready']
    hibernated_clusters = []
    for cluster in clusters_to_hibernate:
        print('starting with', cluster.name, cluster.type)
        hibernate_cluster(cluster.name)
        hibernated_clusters.append(cluster.__dict__)
        # print(f'Hibernated {cluster.name}')

    print(json.dumps(hibernated_clusters, indent=4))


if __name__ == '__main__':
    main()