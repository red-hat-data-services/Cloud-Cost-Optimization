import json

import boto3
import utils
from resume_cluster import resume_cluster, resume_hypershift_cluster, resume_ipi_cluster


def get_last_hibernated():
    s3 = boto3.client('s3')
    s3.download_file('rhods-devops', 'Cloud-Cost-Optimization/Weekend-Hibernation/hibernated_latest.json', 'hibernated_latest.json')


def main():
    print("=== Getting all stopped EC2 instances ===", flush=True)
    ec2_instances = {}
    utils.get_all_instances(ec2_instances, 'stopped')
    get_last_hibernated()

    print("=== Identifying clusters to resume ===", flush=True)
    clusters_to_resume = []
    clusters = json.load(open('hibernated_latest.json'))
    for cluster in clusters:
        clusters_to_resume.append(utils.OcCluster(cluster))

    print("=== Resuming clusters ===", flush=True)
    resumed_clusters = []
    for cluster in clusters_to_resume:
        print('starting with', cluster.name, cluster.type)
        if cluster.hcp == "false":
            if cluster.type == 'ocp':
                resume_ipi_cluster(cluster, ec2_instances[cluster.region], wait_for_ready=False)
                print("IPI - ", cluster.name)
            else:
                resume_cluster(cluster)
                print("OSD or ROSA Classic - ", cluster.name)
        else:
            resume_hypershift_cluster(cluster, ec2_instances[cluster.region], wait_for_ready=False)
            print("Hypershift cluster - ", cluster.name)
        resumed_clusters.append(cluster.__dict__)
        print(f'Hibernated {cluster.name}')


if __name__ == '__main__':
    main()
