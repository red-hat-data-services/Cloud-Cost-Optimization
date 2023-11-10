import json
import boto3
import os

# need to sync the list with latest status, and resume it only if status is Hibernating

class oc_cluster:
    def __init__(self, cluster_detail):
        self.id = cluster_detail['id']
        self.name = cluster_detail['name']
        self.api_url = cluster_detail['api_url']
        self.ocp_version = cluster_detail['ocp_version']
        self.type = cluster_detail['type']
        self.hcp = cluster_detail['hcp']
        self.cloud_provider = cluster_detail['cloud_provider']
        self.region = cluster_detail['region']
        self.status = cluster_detail['status']
        self.resume_error = ''
        self.ocm_account = cluster_detail['ocm_account']
def get_all_cluster_details(ocm_account:str, clusters:dict):
    get_cluster_list(ocm_account)
    clusters_details = open(f'clusters_{ocm_account}.txt').readlines()
    for cluster_detail in clusters_details:
        clusters.append(oc_cluster(cluster_detail, ocm_account))
    clusters = [cluster for cluster in clusters if cluster.cloud_provider == 'aws']

def get_cluster_list(ocm_account:str):
    run_command(f'./get_all_cluster_details.sh {ocm_account}')
def run_command(command):
    print(command)
    output = os.popen(command).read()
    print(output)
    return output

def get_last_hibernated():
    s3 = boto3.client('s3')
    s3.download_file('rhods-devops', 'Cloud-Cost-Optimization/Weekend-Hibernation/hibernated_latest.json', 'hibernated_latest.json')


def hibernate_cluster(cluster: oc_cluster):
    run_command(f'./hybernate_cluster.sh {cluster.ocm_account} {cluster.id}')

def resume_cluster(cluster: oc_cluster):
    run_command(f'./resume_cluster.sh {cluster.ocm_account} {cluster.id}')
def main():
    get_last_hibernated()
    clusters_to_resume = []
    clusters = json.load(open('hibernated_latest.json'))
    for cluster in clusters:
        clusters_to_resume.append(oc_cluster(cluster))
    resumed_clusters = []
    for cluster in clusters_to_resume:
        print('starting with', cluster.name, cluster.type)
        resume_cluster(cluster)
        resumed_clusters.append(cluster.__dict__)
        # print(f'Hibernated {cluster.name}')

    print(json.dumps(resumed_clusters, indent=4))


if __name__ == '__main__':
    main()