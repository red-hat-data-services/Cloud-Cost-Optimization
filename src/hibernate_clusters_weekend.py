import json

import boto3
import utils
from hibernate_cluster import (
    hibernate_cluster,
    hibernate_hypershift_cluster,
    hibernate_ipi_cluster,
    get_all_cluster_details
)


def main():
    ec2_instances = {}

    print("=== Getting all running EC2 instances ===", flush=True)
    utils.get_all_instances(ec2_instances, 'running')

    print("=== Getting details for all clusters ===", flush=True)
    clusters = []
    ocm_accounts = ['PROD', 'STAGE']
    for ocm_account in ocm_accounts:
        get_all_cluster_details(ocm_account, clusters)

    print("=== Identifying clusters to hibernate ===", flush=True)
    clusters_to_hibernate = [cluster for cluster in clusters if cluster.cloud_provider == 'aws' and cluster.status == 'ready']
    for cluster in clusters_to_hibernate:
        print(cluster.name, cluster.type)
    DO_NOT_HIBERNATE_LIST = ['vteam-uat', 'vteam-stage']

    print("=== Hibernating clusters ===", flush=True)
    hibernated_clusters = []
    for cluster in clusters_to_hibernate:
        print('starting with', cluster.name, cluster.type)
        if cluster.name in DO_NOT_HIBERNATE_LIST:
            print(f'skipping the cluster {cluster.name}')
            continue
        elif cluster.hcp == "false":
            if cluster.type == 'ocp':
                hibernate_ipi_cluster(cluster, ec2_instances[cluster.region])
                print('hibernating IPI cluster - ', cluster.name)
            else:
                hibernate_cluster(cluster)
                print("OSD or ROSA Classic - ", cluster.name)
        else:
            hibernate_hypershift_cluster(cluster, ec2_instances[cluster.region], wait_for_stop=False, cleanup_volumes=False)
            print("Hypershift cluster - ", cluster.name)
        hibernated_clusters.append(cluster.__dict__)
        print(f'Hibernated {cluster.name}')

    hibernated_json = json.dumps(hibernated_clusters, indent=4)
    # print(hibernated_json)
    open('hibernated_latest.json', 'w').write(hibernated_json)
    s3 = boto3.client('s3')
    try:
        s3.upload_file('hibernated_latest.json', 'rhods-devops', 'Cloud-Cost-Optimization/Weekend-Hibernation/hibernated_latest.json')
    except Exception as e:
        print(e)


if __name__ == '__main__':
    main()
