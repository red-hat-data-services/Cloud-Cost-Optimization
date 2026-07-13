from concurrent.futures import ThreadPoolExecutor, as_completed

import utils
from hibernate_cluster import get_all_cluster_details
from resume_cluster import resume_generic_cluster


def main():
    print("=== Getting details for all clusters ===", flush=True)
    clusters: list[utils.OcCluster] = []
    ocm_accounts = ['PROD', 'STAGE']
    for ocm_account in ocm_accounts:
        clusters += get_all_cluster_details(ocm_account)

    print("=== Getting all clusters from Smartsheet ===", flush=True)
    smartsheet_data = utils.get_clusters_from_smartsheet()
    for cluster in clusters:
        if cluster.id in smartsheet_data:
            cluster_inactive_hours_end = smartsheet_data[cluster.id][utils.ClusterSmartsheetColumns.INACTIVE_HOURS_END]

            if cluster_inactive_hours_end:
                if cluster_inactive_hours_end.count(':') < 1:
                    print(f'Invalid inactive_hours_end {cluster_inactive_hours_end} for cluster {cluster.name}')
                    continue
                if cluster_inactive_hours_end.count(':') == 1:
                    cluster_inactive_hours_end += ':00'
                cluster.inactive_hours_end = cluster_inactive_hours_end

    print("=== Resuming clusters ===", flush=True)
    clusters_to_resume = [c for c in clusters
                          if c.inactive_hours_end and utils.within_two_hour_window_after(c.inactive_hours_end, default_decision=False)]

    resumed_clusters = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_cluster = {executor.submit(resume_generic_cluster, c): c for c in clusters_to_resume}
        for future in as_completed(future_to_cluster):
            cluster = future_to_cluster[future]
            try:
                future.result()
                resumed_clusters.append(cluster.__dict__)
            except Exception as e:
                print(f"ERROR: Failed to resume cluster {cluster.name}: {e}", flush=True)


if __name__ == '__main__':
    main()
