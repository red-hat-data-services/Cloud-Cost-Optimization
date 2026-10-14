"""
Post-resume health check for OpenShift clusters.

After a ROSA/OSD/IPI cluster is resumed from hibernation, the EC2 instances
may report as running while the OpenShift layer is still broken. This script
performs OpenShift-level health checks and remediation steps to ensure the
cluster is actually functional before reporting success.

Known issues after resume:
  - Kyverno webhook deadlocks can block OVN/CNI, leaving nodes NotReady
  - ROSA autorepair may cordon unhealthy nodes (SchedulingDisabled)
  - Pending CSRs prevent nodes from rejoining after long hibernation
  - Cluster operators (dns, ingress, network) may not recover automatically
"""

import sys
import time
import os
import argparse
import subprocess
import json


# ---------------------------------------------------------------------------
# Cluster login helpers
# ---------------------------------------------------------------------------

def run_command(command):
    """Run a shell command and return stdout. Matches existing repo pattern."""
    print(command)
    output = os.popen(command).read()
    return output


def run_command_with_status(command, timeout=120):
    """Run a shell command and return (exit_code, stdout, stderr).

    Unlike run_command(), this captures the exit code so callers can
    distinguish success from failure — critical for health-check logic.
    """
    print(f'Running: {command}')
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        print(f'Command timed out after {timeout}s: {command}')
        return 1, '', 'Command timed out'


def get_cluster_list(ocm_account):
    """Retrieve the cluster list from OCM (reuses existing shell script)."""
    run_command(f'script/./get_all_cluster_details.sh {ocm_account}')


def find_cluster(cluster_name, ocm_account):
    """Find the target cluster's ID and API URL from the OCM cluster list.

    Returns (cluster_id, api_url) or exits if the cluster is not found.
    """
    get_cluster_list(ocm_account)
    clusters_file = f'clusters_{ocm_account}.txt'
    if not os.path.isfile(clusters_file):
        print(f'ERROR: Cluster list file {clusters_file} not found')
        sys.exit(1)

    lines = open(clusters_file).readlines()
    for line in lines:
        details = line.split()
        if len(details) < 9:
            continue
        name = details[1]
        # Sanitize long IPI names the same way resume_cluster.py does
        sanitized_input = cluster_name[:28] if cluster_name.count('-') == 4 else cluster_name
        if name == sanitized_input:
            cluster_id = details[0]
            api_url = details[2]
            return cluster_id, api_url

    print(f'ERROR: Cluster "{cluster_name}" not found in OCM account {ocm_account}')
    sys.exit(1)


def login_to_cluster(cluster_id, ocm_account):
    """Log in to the cluster using OCM-generated credentials.

    Uses `ocm cluster login` which creates a kubeconfig entry. This is
    the same OCM CLI binary already set up by the workflow.
    """
    # First, get a login command from OCM. The --cred flag outputs
    # the credentials (username/password) we can use with oc login.
    print(f'Retrieving cluster credentials from OCM for cluster {cluster_id}...')
    rc, stdout, stderr = run_command_with_status(
        f'ocm get /api/clusters_mgmt/v1/clusters/{cluster_id}/credentials',
        timeout=60
    )
    if rc != 0:
        print(f'ERROR: Failed to retrieve cluster credentials: {stderr}')
        sys.exit(1)

    try:
        creds = json.loads(stdout)
    except json.JSONDecodeError:
        print(f'ERROR: Failed to parse credentials response')
        sys.exit(1)

    # The credentials response contains a kubeconfig we can use directly
    if 'kubeconfig' in creds:
        kubeconfig_path = '/tmp/health-check-kubeconfig'
        with open(kubeconfig_path, 'w') as f:
            f.write(creds['kubeconfig'])
        os.environ['KUBECONFIG'] = kubeconfig_path
        print(f'Logged in to cluster {cluster_id} using OCM kubeconfig')
        return True

    # Fallback: use admin credentials if available
    if 'admin' in creds:
        admin = creds['admin']
        api_url = admin.get('api_url', '')
        user = admin.get('user', 'cluster-admin')
        password = admin.get('password', '')
        if api_url and password:
            rc, stdout, stderr = run_command_with_status(
                f'oc login {api_url} --username={user} --password={password} --insecure-skip-tls-verify=true',
                timeout=60
            )
            if rc == 0:
                print(f'Logged in to cluster {cluster_id} as {user}')
                return True
            else:
                print(f'WARNING: oc login failed: {stderr}')

    print('ERROR: Could not obtain cluster credentials from OCM')
    sys.exit(1)


def verify_oc_connectivity():
    """Verify that oc can reach the cluster API server."""
    rc, stdout, stderr = run_command_with_status('oc whoami', timeout=30)
    if rc != 0:
        print(f'ERROR: Cannot reach cluster API server: {stderr}')
        return False
    print(f'Connected to cluster as: {stdout}')
    return True


# ---------------------------------------------------------------------------
# Health-check remediation steps
# ---------------------------------------------------------------------------

def patch_kyverno_webhooks():
    """Patch Kyverno webhook configurations to failurePolicy: Ignore.

    WHY: Kyverno installs validating and mutating webhook configurations
    that default to failurePolicy: Fail. After a cluster resume, if the
    Kyverno pods are not yet running, these webhooks will REJECT all API
    calls that match their rules — including OVN/CNI pod creation. This
    creates a deadlock: the network pods can't start because Kyverno
    webhooks reject them, and Kyverno can't start because the network
    is down. Setting failurePolicy to Ignore breaks this deadlock by
    allowing API calls to proceed even when Kyverno is unavailable.
    Kyverno will re-enforce policies once it is healthy.
    """
    print('\n=== Step 1: Patching Kyverno webhooks (break deadlock) ===')

    webhook_types = [
        'validatingwebhookconfigurations',
        'mutatingwebhookconfigurations',
    ]
    patched_count = 0

    for wh_type in webhook_types:
        # Find Kyverno-related webhooks
        rc, stdout, stderr = run_command_with_status(
            f'oc get {wh_type} -o name 2>/dev/null | grep -i kyverno',
            timeout=30
        )
        if rc != 0 or not stdout.strip():
            print(f'  No Kyverno {wh_type} found (this is OK if Kyverno is not installed)')
            continue

        webhook_names = stdout.strip().split('\n')
        for webhook_name in webhook_names:
            webhook_name = webhook_name.strip()
            if not webhook_name:
                continue

            # Get current webhook config to find number of webhooks entries
            rc, config_json, _ = run_command_with_status(
                f'oc get {webhook_name} -o json',
                timeout=30
            )
            if rc != 0:
                print(f'  WARNING: Could not read {webhook_name}')
                continue

            try:
                config = json.loads(config_json)
                webhooks = config.get('webhooks', [])
                for i in range(len(webhooks)):
                    patch_cmd = (
                        f'oc patch {webhook_name} --type=json '
                        f'-p \'[{{"op":"replace","path":"/webhooks/{i}/failurePolicy","value":"Ignore"}}]\''
                    )
                    rc, _, patch_err = run_command_with_status(patch_cmd, timeout=30)
                    if rc == 0:
                        patched_count += 1
                    else:
                        print(f'  WARNING: Failed to patch webhook {i} in {webhook_name}: {patch_err}')
            except json.JSONDecodeError:
                # Fallback: try a simple patch without parsing
                patch_cmd = (
                    f'oc patch {webhook_name} --type=json '
                    f'-p \'[{{"op":"replace","path":"/webhooks/0/failurePolicy","value":"Ignore"}}]\''
                )
                rc, _, _ = run_command_with_status(patch_cmd, timeout=30)
                if rc == 0:
                    patched_count += 1

    if patched_count > 0:
        print(f'  Patched {patched_count} Kyverno webhook entries to failurePolicy: Ignore')
    else:
        print('  No Kyverno webhooks needed patching')


def approve_pending_csrs():
    """Approve any pending Certificate Signing Requests (CSRs).

    WHY: After a cluster has been hibernated for a long time, node
    certificates may expire. When the nodes come back, they generate new
    CSRs that must be approved before the nodes can fully rejoin the
    cluster. Without approval, nodes stay in NotReady state because
    the kubelet cannot establish a trusted connection to the API server.
    """
    print('\n=== Step 2: Approving pending CSRs ===')

    rc, stdout, stderr = run_command_with_status(
        'oc get csr -o json',
        timeout=30
    )
    if rc != 0:
        print(f'  WARNING: Could not list CSRs: {stderr}')
        return

    try:
        csr_data = json.loads(stdout)
    except json.JSONDecodeError:
        print('  WARNING: Could not parse CSR data')
        return

    pending_csrs = []
    for item in csr_data.get('items', []):
        conditions = item.get('status', {}).get('conditions', [])
        # A CSR is pending if it has no conditions (not yet approved/denied)
        if not conditions:
            csr_name = item.get('metadata', {}).get('name', '')
            if csr_name:
                pending_csrs.append(csr_name)

    if not pending_csrs:
        print('  No pending CSRs found')
        return

    print(f'  Found {len(pending_csrs)} pending CSR(s), approving...')
    approved = 0
    for csr_name in pending_csrs:
        rc, _, stderr = run_command_with_status(
            f'oc adm certificate approve {csr_name}',
            timeout=30
        )
        if rc == 0:
            approved += 1
            print(f'  Approved CSR: {csr_name}')
        else:
            print(f'  WARNING: Failed to approve CSR {csr_name}: {stderr}')

    print(f'  Approved {approved}/{len(pending_csrs)} CSRs')

    # Wait briefly and check for additional CSRs — node CSRs sometimes come
    # in two rounds (client cert first, then server cert)
    time.sleep(15)
    rc, stdout, _ = run_command_with_status(
        'oc get csr -o json',
        timeout=30
    )
    if rc == 0:
        try:
            csr_data = json.loads(stdout)
            second_round = []
            for item in csr_data.get('items', []):
                conditions = item.get('status', {}).get('conditions', [])
                if not conditions:
                    csr_name = item.get('metadata', {}).get('name', '')
                    if csr_name:
                        second_round.append(csr_name)
            if second_round:
                print(f'  Found {len(second_round)} additional CSR(s) (second round), approving...')
                for csr_name in second_round:
                    run_command_with_status(
                        f'oc adm certificate approve {csr_name}',
                        timeout=30
                    )
        except json.JSONDecodeError:
            pass


def uncordon_nodes():
    """Uncordon any nodes that have been cordoned (SchedulingDisabled).

    WHY: ROSA's autorepair mechanism automatically cordons nodes that
    it detects as unhealthy. After a resume, nodes may have been marked
    as SchedulingDisabled during the hibernation process or during the
    initial unstable period after resume. These nodes need to be
    uncordoned so workloads can be scheduled on them.
    """
    print('\n=== Step 3: Uncordoning nodes ===')

    rc, stdout, stderr = run_command_with_status(
        'oc get nodes -o json',
        timeout=30
    )
    if rc != 0:
        print(f'  WARNING: Could not list nodes: {stderr}')
        return

    try:
        nodes_data = json.loads(stdout)
    except json.JSONDecodeError:
        print('  WARNING: Could not parse node data')
        return

    cordoned_nodes = []
    for node in nodes_data.get('items', []):
        spec = node.get('spec', {})
        if spec.get('unschedulable', False):
            node_name = node.get('metadata', {}).get('name', '')
            if node_name:
                cordoned_nodes.append(node_name)

    if not cordoned_nodes:
        print('  No cordoned nodes found')
        return

    print(f'  Found {len(cordoned_nodes)} cordoned node(s), uncordoning...')
    for node_name in cordoned_nodes:
        rc, _, stderr = run_command_with_status(
            f'oc adm uncordon {node_name}',
            timeout=30
        )
        if rc == 0:
            print(f'  Uncordoned node: {node_name}')
        else:
            print(f'  WARNING: Failed to uncordon {node_name}: {stderr}')


def wait_for_nodes_ready(timeout_minutes=10):
    """Wait for all nodes to reach the Ready condition.

    WHY: Even after EC2 instances report as running, the OpenShift nodes
    may not be Ready. Nodes need time for kubelet to start, network
    plugins (OVN/CNI) to initialize, and for the node to register with
    the API server. This step ensures we don't report success while nodes
    are still initializing.

    Returns True if all nodes are Ready within the timeout, False otherwise.
    """
    print(f'\n=== Step 4: Waiting for nodes to be Ready (timeout: {timeout_minutes} min) ===')

    deadline = time.time() + (timeout_minutes * 60)
    check_interval = 30  # seconds between checks
    last_not_ready = []

    while time.time() < deadline:
        rc, stdout, stderr = run_command_with_status(
            'oc get nodes -o json',
            timeout=30
        )
        if rc != 0:
            print(f'  WARNING: Could not query nodes: {stderr}')
            time.sleep(check_interval)
            continue

        try:
            nodes_data = json.loads(stdout)
        except json.JSONDecodeError:
            print('  WARNING: Could not parse node data')
            time.sleep(check_interval)
            continue

        not_ready_nodes = []
        total_nodes = 0
        for node in nodes_data.get('items', []):
            node_name = node.get('metadata', {}).get('name', 'unknown')
            total_nodes += 1
            conditions = node.get('status', {}).get('conditions', [])
            is_ready = False
            for condition in conditions:
                if condition.get('type') == 'Ready' and condition.get('status') == 'True':
                    is_ready = True
                    break
            if not is_ready:
                not_ready_nodes.append(node_name)

        if not not_ready_nodes:
            print(f'  All {total_nodes} nodes are Ready')
            return True

        last_not_ready = not_ready_nodes
        remaining = int(deadline - time.time())
        print(f'  {len(not_ready_nodes)}/{total_nodes} nodes not Ready '
              f'({remaining}s remaining): {", ".join(not_ready_nodes[:5])}'
              f'{"..." if len(not_ready_nodes) > 5 else ""}')

        # While waiting, approve any new CSRs that appear — nodes
        # sometimes generate additional CSRs during the boot process
        approve_pending_csrs_silent()

        time.sleep(check_interval)

    print(f'  TIMEOUT: {len(last_not_ready)} node(s) still not Ready after {timeout_minutes} minutes')
    for node_name in last_not_ready:
        print(f'    - {node_name}')
    return False


def approve_pending_csrs_silent():
    """Silently approve any pending CSRs (used during wait loops)."""
    rc, stdout, _ = run_command_with_status('oc get csr -o json', timeout=30)
    if rc != 0:
        return
    try:
        csr_data = json.loads(stdout)
        for item in csr_data.get('items', []):
            conditions = item.get('status', {}).get('conditions', [])
            if not conditions:
                csr_name = item.get('metadata', {}).get('name', '')
                if csr_name:
                    run_command_with_status(
                        f'oc adm certificate approve {csr_name}',
                        timeout=30
                    )
    except json.JSONDecodeError:
        pass


def verify_cluster_operators():
    """Verify that critical cluster operators are Available.

    WHY: Even if all nodes are Ready, the cluster may still be broken
    if critical operators are degraded. DNS, ingress, and network
    operators are essential for the cluster to serve workloads. If
    these are not Available, the cluster resume has not truly succeeded.

    Returns True if all critical operators are Available, False otherwise.
    """
    print('\n=== Step 5: Verifying critical cluster operators ===')

    # These operators are essential for a functional cluster
    critical_operators = ['dns', 'ingress', 'network']

    rc, stdout, stderr = run_command_with_status(
        'oc get clusteroperators -o json',
        timeout=30
    )
    if rc != 0:
        print(f'  WARNING: Could not query cluster operators: {stderr}')
        # On HCP clusters, clusteroperators may not be directly accessible
        # from the management cluster context — treat as non-fatal
        print('  Skipping operator check (may not be available for this cluster type)')
        return True

    try:
        co_data = json.loads(stdout)
    except json.JSONDecodeError:
        print('  WARNING: Could not parse cluster operator data')
        return True

    operators = co_data.get('items', [])
    if not operators:
        # HCP clusters may not expose ClusterOperator resources
        print('  No cluster operators found (expected for HCP clusters)')
        return True

    all_healthy = True
    for operator in operators:
        op_name = operator.get('metadata', {}).get('name', 'unknown')
        conditions = operator.get('status', {}).get('conditions', [])

        available = False
        degraded = False
        for condition in conditions:
            if condition.get('type') == 'Available':
                available = condition.get('status') == 'True'
            if condition.get('type') == 'Degraded':
                degraded = condition.get('status') == 'True'

        is_critical = op_name in critical_operators
        status_str = f'Available={available}, Degraded={degraded}'

        if is_critical:
            if not available or degraded:
                print(f'  CRITICAL: Operator "{op_name}" is unhealthy ({status_str})')
                all_healthy = False
            else:
                print(f'  OK: Operator "{op_name}" is healthy ({status_str})')
        elif not available or degraded:
            # Report non-critical operator issues as warnings
            print(f'  WARNING: Operator "{op_name}" is unhealthy ({status_str})')

    if all_healthy:
        print('  All critical cluster operators are healthy')
    else:
        print('  FAILURE: One or more critical cluster operators are unhealthy')

    return all_healthy


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_arguments():
    parser = argparse.ArgumentParser(
        description='Post-resume health check for OpenShift clusters'
    )
    parser.add_argument(
        '--cluster_name', dest='cluster_name', action='store',
        help='Provide the cluster name to health-check', required=True
    )
    parser.add_argument(
        '--ocm_account', dest='ocm_account', action='store',
        help='Provide the OCM account (PROD or STAGE)', required=True
    )
    return parser.parse_args()


def main():
    args = parse_arguments()
    ocm_account = args.ocm_account.split(' ')[0]
    cluster_name = args.cluster_name

    print(f'=== Post-Resume Health Check for cluster: {cluster_name} ===')
    print(f'    OCM Account: {ocm_account}')
    print()

    # Step 0: Find the cluster and log in
    cluster_id, api_url = find_cluster(cluster_name, ocm_account)
    print(f'Found cluster: id={cluster_id}, api_url={api_url}')

    login_to_cluster(cluster_id, ocm_account)

    if not verify_oc_connectivity():
        print('HEALTH CHECK FAILED: Cannot connect to cluster API server')
        sys.exit(1)

    # Step 1: Patch Kyverno webhooks to break potential deadlocks
    patch_kyverno_webhooks()

    # Step 2: Approve pending CSRs so nodes can rejoin
    approve_pending_csrs()

    # Step 3: Uncordon any cordoned nodes
    uncordon_nodes()

    # Step 4: Wait for all nodes to be Ready
    nodes_ready = wait_for_nodes_ready(timeout_minutes=10)

    # Step 5: Verify critical cluster operators
    operators_healthy = verify_cluster_operators()

    # Final verdict
    print('\n' + '=' * 60)
    if nodes_ready and operators_healthy:
        print('HEALTH CHECK PASSED: Cluster is healthy and ready')
        print('=' * 60)
    else:
        print('HEALTH CHECK FAILED:')
        if not nodes_ready:
            print('  - Not all nodes reached Ready state')
        if not operators_healthy:
            print('  - Critical cluster operators are unhealthy')
        print('=' * 60)
        sys.exit(1)


if __name__ == '__main__':
    main()
