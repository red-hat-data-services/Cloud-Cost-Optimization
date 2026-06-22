import datetime
import utils
from hibernate_cluster import (
    hibernate_cluster,
    hibernate_hypershift_cluster,
    hibernate_ipi_cluster,
    get_all_cluster_details
)

def main():
    print("=== Getting all running EC2 instances ===", flush=True)
    ec2_instances = {}
    utils.get_all_instances(ec2_instances, 'running')

    print("=== Getting details for all clusters ===", flush=True)
    clusters:list[utils.OcCluster] = []
    ocm_accounts = ['PROD', 'STAGE']
    for ocm_account in ocm_accounts:
        get_all_cluster_details(ocm_account, clusters)

    print("=== Getting all clusters from Smartsheet ===", flush=True)
    smartsheet_data = utils.get_clusters_from_smartsheet()

    # updating inactive_hours_start for each cluster based on smartsheet
    for cluster in clusters:
        if cluster.id not in smartsheet_data:
            print(f'{cluster.name} ({cluster.id}) not found in smartsheet data')
            continue
        
        cluster_inactive_hours_start = smartsheet_data[cluster.id][utils.ClusterSmartsheetColumns.INACTIVE_HOURS_START]
       
        if not cluster_inactive_hours_start:
            print(f'Start time not found for {cluster.name}')
            continue
        if cluster_inactive_hours_start.count(':') < 1:
            print(f'Invalid inactive_hours_start {cluster_inactive_hours_start} for cluster {cluster.name}')
            continue
        
        # checking to see if smartsheet time entry is missing the seconds part
        if cluster_inactive_hours_start.count(':') == 1:
            cluster_inactive_hours_start += ':00'
        cluster.inactive_hours_start = cluster_inactive_hours_start

    hibernated_clusters = []
    no_action_clusters = []

    print("=== Hibernating clusters ===", flush=True)
    for cluster in clusters:
        # default to hibernating the cluster immediately if inactive_hours are set incorrectly
        if cluster.inactive_hours_start and utils.within_two_hour_window_after(cluster.inactive_hours_start, default_decision=True):
            if cluster.hcp == "false":
                if cluster.type == 'ocp':
                    print("Hibernating IPI Cluster - ", cluster.name)
                    hibernate_ipi_cluster(cluster, ec2_instances[cluster.region])
                else:
                    print("Hibernating OSD or ROSA Classic Cluster - ", cluster.name)
                    hibernate_cluster(cluster)
            else:
                hibernate_hypershift_cluster(cluster, ec2_instances[cluster.region], wait_for_stop=False, cleanup_volumes=False)
                print("Hibernating Hypershift Cluster - ", cluster.name)
            hibernated_clusters.append(cluster.__dict__)
        else:
            no_action_clusters.append(cluster.__dict__)

if __name__ == '__main__':
    main()
