import json
import requests
import boto3
import traceback

import utils

def get_all_cluster_details(ocm_account:str, clusters:dict):
    utils.get_cluster_list(ocm_account)
    clusters_details = open(f'clusters_{ocm_account}.txt').readlines()
    for cluster_detail in clusters_details:
        clusters.append(utils.OcCluster(cluster_detail, ocm_account))
    clusters = [cluster for cluster in clusters if cluster.cloud_provider == 'aws']


def check_instance_status(cluster:utils.OcCluster, ec2_running_map:dict, ec2_stopped_map:dict):
    InstanceIds_running = [ec2_running_map[worker_node]['InstanceId'] for worker_node in ec2_running_map if
                   utils.worker_node_belongs_to_the_hcp_cluster(ec2_running_map[worker_node], cluster.name)]
    InstanceIds_stopped = [ec2_stopped_map[worker_node]['InstanceId'] for worker_node in ec2_stopped_map if
                   utils.worker_node_belongs_to_the_hcp_cluster(ec2_stopped_map[worker_node], cluster.name)]

    ec2_client = boto3.client('ec2', region_name=cluster.region)

    # detach and delete non-root volumes
    root_devices = {inst['InstanceId']: inst['RootDeviceName'] for inst in ec2_stopped_map.values() if inst['InstanceId'] in InstanceIds_stopped}
    filters = [{'Name': 'attachment.instance-id', 'Values': InstanceIds_stopped}]
    attached_volumes = ec2_client.describe_volumes(Filters=filters)
    if attached_volumes['Volumes']:
        attached_volumes = [attachment for volume in attached_volumes['Volumes'] for attachment in volume['Attachments']
                            if attachment['DeleteOnTermination'] == True
                            and attachment['Device'] != root_devices.get(attachment['InstanceId'])
                            and not utils.check_if_given_tag_exists('KubernetesCluster', volume.get('Tags', []))]
        print('attached_volumes', attached_volumes)
        for volume in attached_volumes:
            print(f'detaching the volume {volume["VolumeId"]}')
            ec2_client.detach_volume(Device=volume['Device'], InstanceId=volume['InstanceId'], VolumeId=volume['VolumeId'])
        for volume in attached_volumes:
            print(f'deleting the volume {volume["VolumeId"]}')
            utils.delete_volume(volume['VolumeId'], cluster.region)
    if len(InstanceIds_running) == 0 and len(InstanceIds_stopped) == 0:
        try:
            print(f'starting node pool sync for {cluster.name}')
            sync_hcp_node_pools(cluster)
        except Exception as e:
            print(traceback.format_exc())
            print('error while syncing the machine pools for HCP cluster', cluster.name)


def sync_hcp_node_pools(cluster:utils.OcCluster):
    api_server_base_url =  'https://api.openshift.com/api' if cluster.ocm_account == 'PROD' else 'https://api.stage.openshift.com/api'
    ocm_api_token = utils.get_ocm_api_token()
    node_pools_response = requests.get(f'{api_server_base_url}/clusters_mgmt/v1/clusters/{cluster.id}/node_pools', headers={'Authorization': f'Bearer {ocm_api_token}'})
    node_pools = node_pools_response.json()
    node_pools = {node_pool['id']:node_pool['replicas'] for node_pool in node_pools['items'] if node_pool['kind'] == 'NodePool'}
    totalNodes = 0
    for id, replicas in node_pools.items():
        newReplicas = replicas+1 if replicas <= 2 else replicas-1
        payload = {'id': id,'labels':{},'taints':[],'replicas': newReplicas}
        response = requests.patch(f'{api_server_base_url}/clusters_mgmt/v1/clusters/{cluster.id}/node_pools/{id}',
                       data=json.dumps(payload),
                     headers={'Authorization': f'Bearer {ocm_api_token}', 'Content-Type': 'application/json'})

        print(f'synced the machine pool {id} with the new replica count {newReplicas} for cluster {cluster.name}')
        print(response.status_code)
        if response.status_code == 200:
            totalNodes += newReplicas
            print(f'now total nodes are {totalNodes}')

        # instantly resetting the node count to avoid additional cost
        payload = {'id': id,'labels':{},'taints':[],'replicas': replicas}
        response = requests.patch(f'{api_server_base_url}/clusters_mgmt/v1/clusters/{cluster.id}/node_pools/{id}',
                       data=json.dumps(payload),
                     headers={'Authorization': f'Bearer {ocm_api_token}', 'Content-Type': 'application/json'})

        print(f'reset the machine pool {id} with the original replica count {replicas} for cluster {cluster.name}')
        print(response.status_code)
        if response.status_code == 200:
            totalNodes += replicas - newReplicas
            print(f'now total nodes are back to {totalNodes}')

    return totalNodes


def main():
    ec2_running_instances = {}
    utils.get_all_instances(ec2_running_instances, 'running')
    ec2_stopped_instances = {}
    utils.get_all_instances(ec2_stopped_instances, 'stopped')

    clusters = []
    ocm_accounts = ['PROD', 'STAGE']

    for ocm_account in ocm_accounts:
        get_all_cluster_details(ocm_account, clusters)

    clusters_to_hibernate = [cluster for cluster in clusters if (cluster.type == 'osd' or (cluster.type == 'rosa')) and cluster.status == 'ready']
    print('cluster to hibernate')
    for cluster in clusters_to_hibernate:
        print(cluster.name, cluster.type)

    hibernated_clusters = []
    for cluster in clusters_to_hibernate:
        print('starting with', cluster.name, cluster.type)
        if cluster.hcp == "true":
            check_instance_status(cluster, ec2_running_instances[cluster.region], ec2_stopped_instances[cluster.region])
            print("Hypershift cluster - ", cluster.name)
        hibernated_clusters.append(cluster.__dict__)
        print(f'Hibernated {cluster.name}')
    hibernated_json = json.dumps(hibernated_clusters, indent=4)


if __name__ == '__main__':
    main()
