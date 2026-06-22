import datetime
import utils
from hibernate_cluster import get_all_cluster_details
from resume_cluster import resume_cluster, resume_hypershift_cluster, resume_ipi_cluster


def main():
    print("=== Getting all stopped EC2 instances ===", flush=True)
    ec2_instances = {}
    utils.get_all_instances(ec2_instances, 'stopped')

    print("=== Getting details for all clusters ===", flush=True)
    clusters: list[utils.OcCluster] = []
    ocm_accounts = ['PROD', 'STAGE']
    for ocm_account in ocm_accounts:
        get_all_cluster_details(ocm_account, clusters)

    print("=== Getting all clusters from Smartsheet ===", flush=True)
    smartsheet_data = utils.get_clusters_from_smartsheet()
    for cluster in clusters:
        if cluster.id in smartsheet_data:
            cluster_inactive_hours_end = smartsheet_data[cluster.id][utils.ClusterSmartsheetColumns.INACTIVE_HOURS_END]

            if cluster_inactive_hours_end[1]:
                if cluster_inactive_hours_end[1].count(':') < 1:
                    print(f'Invalid inactive_hours_end {cluster_inactive_hours_end[1]} for cluster {cluster.name}')
                    continue
                if cluster_inactive_hours_end[1].count(':') == 1:
                    cluster_inactive_hours_end[1] += ':00'
                cluster.inactive_hours_end = cluster_inactive_hours_end[1]

    print("=== Resuming clusters ===", flush=True)
    resumed_clusters = []
    for cluster in clusters:
        if cluster.inactive_hours_end and utils.within_two_hour_window_after(cluster.inactive_hours_end, default_decision=False):
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


if __name__ == '__main__':
    main()
