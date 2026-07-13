import utils
from utils import InstanceState

from hibernate_cluster import (
    hibernate_cluster,
    hibernate_hypershift_cluster,
    hibernate_ipi_cluster,
    get_all_cluster_details
)


def main():
    ec2_instances = {}

    print("=== Getting all running EC2 instances ===", flush=True)
    utils.get_all_instances(ec2_instances, [InstanceState.running, InstanceState.pending])

    clusters:list[utils.OcCluster] = []
    ocm_accounts = ['PROD', 'STAGE']

    print("=== Getting details for all clusters ===", flush=True)
    for ocm_account in ocm_accounts:
        get_all_cluster_details(ocm_account, clusters)

    print("=== Identifying clusters to hibernate ===", flush=True)
    clusters_to_hibernate = [cluster for cluster in clusters if cluster.cloud_provider == 'aws' and cluster.status == 'ready']
    for cluster in clusters_to_hibernate:
        print(cluster.name, cluster.type)
    DO_NOT_HIBERNATE_LIST = ['vteam-uat', 'vteam-stage']


    print("=== Getting all clusters from Smartsheet ===", flush=True)
    smartsheet_data = utils.get_clusters_from_smartsheet()

    print("=== Hibernating clusters ===", flush=True)
    hibernated_clusters = []
    for cluster in clusters_to_hibernate:
        if (cluster.id in smartsheet_data
                and not smartsheet_data[cluster.id][utils.ClusterSmartsheetColumns.INACTIVE_HOURS_START]
                and smartsheet_data[cluster.id][utils.ClusterSmartsheetColumns.STATUS] == 'ready'):
            print('starting with', cluster.name, cluster.type)
            if cluster.name in DO_NOT_HIBERNATE_LIST:
                print(f'skipping the cluster {cluster.name}')
                continue
            if cluster.hcp == "false":
                if cluster.type == 'ocp':
                    hibernate_ipi_cluster(cluster, ec2_instances[cluster.region])
                    print("IPI - ", cluster.name)
                else:
                    hibernate_cluster(cluster)
                    print("OSD or ROSA Classic - ", cluster.name)
            else:
                hibernate_hypershift_cluster(cluster, ec2_instances[cluster.region], wait_for_stop=False, cleanup_volumes=False)
                print("Hypershift cluster - ", cluster.name)
            hibernated_clusters.append(cluster.__dict__)


if __name__ == '__main__':
    main()
