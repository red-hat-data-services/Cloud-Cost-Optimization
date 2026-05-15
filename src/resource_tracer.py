"""
AWS Resource Tracer

Read-only inspection tool that traces AWS resources back to their OpenShift
clusters and identifies who provisioned them. Currently supports EC2 instances.

Usage:
  python src/resource_tracer.py i-01d154018d489351c --region us-east-1
  python src/resource_tracer.py --scan --region us-east-1
  python src/resource_tracer.py --scan --region us-west-2 --skip-ocm
"""

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict

import boto3
from botocore.exceptions import ClientError, NoCredentialsError


@dataclass
class ResourceTrace:
    resource_id: str
    resource_type: str
    instance_type: str = ""
    state: str = ""
    name: str = ""
    launch_time: str = ""
    age_str: str = ""
    cluster_name: str = ""
    cluster_type: str = "unknown"
    cluster_id: str = ""
    owner_name: str = ""
    owner_email: str = ""
    ocm_account: str = ""
    expiration_date: str = ""
    is_expired: bool = False
    expired_ago: str = ""
    prow_job: str = ""
    prow_build_id: str = ""
    prunability: str = ""
    tags: dict = field(default_factory=dict)


class ResourceTracer:
    def __init__(self, region="us-east-1", ocm_accounts=None, skip_ocm=False):
        self.region = region
        self.ocm_accounts = ocm_accounts or ["PROD", "STAGE"]
        self.skip_ocm = skip_ocm
        self.ocm_clusters = {}
        self.owner_cache = {}

        try:
            self.session = boto3.Session(region_name=region)
            self.ec2_client = self.session.client("ec2")
        except NoCredentialsError:
            print("Error: AWS credentials not found.")
            sys.exit(1)

    # ── EC2 fetching ──────────────────────────────────────────────

    def fetch_ec2_instances(self, instance_ids=None, states=None):
        instances = []
        paginator = self.ec2_client.get_paginator("describe_instances")

        try:
            if instance_ids:
                pages = paginator.paginate(InstanceIds=instance_ids)
            elif states:
                pages = paginator.paginate(
                    Filters=[{"Name": "instance-state-name", "Values": states}]
                )
            else:
                pages = paginator.paginate()

            for page in pages:
                for reservation in page["Reservations"]:
                    instances.extend(reservation["Instances"])

        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "InvalidInstanceID.NotFound":
                print(f"Warning: {e.response['Error']['Message']}")
            else:
                print(f"Error fetching instances: {e}")

        return instances

    # ── Tag helpers ───────────────────────────────────────────────

    def _tags_to_dict(self, instance):
        return {t["Key"]: t["Value"] for t in instance.get("Tags", [])}

    def _get_kubernetes_cluster_name(self, tags):
        for key, value in tags.items():
            if key.startswith("kubernetes.io/cluster/") and value == "owned":
                return key.split("kubernetes.io/cluster/")[1]
        return None

    def _parse_expiration(self, tags):
        raw = tags.get("expirationDate")
        if not raw:
            return None, False
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return dt, now > dt
        except (ValueError, TypeError):
            return None, False

    def _format_age(self, dt):
        if not dt:
            return ""
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return ""
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = now - dt
        days = delta.days
        hours = delta.seconds // 3600
        if days > 0:
            return f"{days}d {hours}h"
        return f"{hours}h"

    # ── Classification ────────────────────────────────────────────

    def classify_ec2_instance(self, instance):
        tags = self._tags_to_dict(instance)
        k8s_cluster = self._get_kubernetes_cluster_name(tags)
        exp_dt, is_expired = self._parse_expiration(tags)
        launch_time = instance.get("LaunchTime")

        trace = ResourceTrace(
            resource_id=instance["InstanceId"],
            resource_type="ec2",
            instance_type=instance.get("InstanceType", ""),
            state=instance.get("State", {}).get("Name", ""),
            name=tags.get("Name", ""),
            launch_time=launch_time.isoformat() if launch_time else "",
            age_str=self._format_age(launch_time),
            tags=tags,
        )

        if exp_dt:
            trace.expiration_date = exp_dt.isoformat()
            trace.is_expired = is_expired
            if is_expired:
                trace.expired_ago = self._format_age(exp_dt)

        # 1. Prow CI
        if "prow.k8s.io/build-id" in tags and "prow.k8s.io/job" in tags:
            trace.cluster_type = "prow-ci"
            trace.prow_job = tags["prow.k8s.io/job"]
            trace.prow_build_id = tags["prow.k8s.io/build-id"]
            trace.cluster_name = k8s_cluster or ""
            return trace

        # 2. ROSA HCP — has api.openshift.com/name
        if "api.openshift.com/name" in tags:
            trace.cluster_name = tags["api.openshift.com/name"]
            trace.cluster_id = tags.get("api.openshift.com/id", "")
            clustertype = tags.get("red-hat-clustertype", "")
            if clustertype == "osd":
                trace.cluster_type = "osd-hcp"
            else:
                trace.cluster_type = "rosa-hcp"
            return trace

        # 3. ROSA Classic / OSD — has red-hat-clustertype but no api.openshift.com/name
        if "red-hat-clustertype" in tags:
            clustertype = tags["red-hat-clustertype"]
            trace.cluster_type = "rosa-classic" if clustertype == "rosa" else "osd"
            trace.cluster_id = tags.get("api.openshift.com/id", "")
            trace.cluster_name = k8s_cluster or ""
            return trace

        # 4. IPI — has kubernetes.io/cluster/* with owned
        if k8s_cluster:
            trace.cluster_type = "ipi"
            trace.cluster_name = k8s_cluster
            return trace

        # 5. Unknown
        trace.cluster_type = "unknown"
        return trace

    # ── OCM integration ──────────────────────────────────────────

    def load_ocm_clusters(self):
        if self.skip_ocm:
            return

        for account in self.ocm_accounts:
            if self._ocm_login(account):
                clusters = self._ocm_list_clusters(account)
                self.ocm_clusters.update(clusters)

    def _ocm_login(self, account):
        token = _get_ocm_token()
        if not token:
            # No token set — try using whatever session is already active
            result = subprocess.run(
                ["ocm", "whoami"], capture_output=True, text=True,
            )
            if result.returncode == 0:
                return True
            print(f"Warning: OCM_TOKEN not set and no active session for {account}")
            return False

        cmd = ["ocm", "login", "--token", token]
        if account == "STAGE":
            cmd.extend(["--url", "stage"])
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Warning: OCM login failed for {account}: {result.stderr.strip()}")
            return False
        return True

    def _ocm_list_clusters(self, account):
        clusters = {}
        page = 1
        page_size = 100

        while True:
            result = subprocess.run(
                ["ocm", "get", "/api/clusters_mgmt/v1/clusters",
                 "--parameter", f"page={page}",
                 "--parameter", f"size={page_size}"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"Warning: Could not list OCM clusters ({account}): {result.stderr.strip()}")
                return clusters

            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                print(f"Warning: Invalid JSON from OCM cluster list ({account})")
                return clusters

            for item in data.get("items", []):
                cluster_id = item.get("id", "")
                name = item.get("name", "")
                entry = {
                    "id": cluster_id,
                    "name": name,
                    "type": item.get("product", {}).get("id", ""),
                    "hcp": str(item.get("hypershift", {}).get("enabled", False)).lower(),
                    "region": item.get("region", {}).get("id", ""),
                    "status": item.get("state", ""),
                    "ocm_account": account,
                }
                clusters[name] = entry
                clusters[cluster_id] = entry

            total = data.get("total", 0)
            if page * page_size >= total:
                break
            page += 1

        return clusters

    def lookup_cluster_owner(self, cluster_id, ocm_account):
        if cluster_id in self.owner_cache:
            return self.owner_cache[cluster_id]

        result = subprocess.run(
            ["ocm", "get", "/api/accounts_mgmt/v1/subscriptions",
             "-p", f"search=cluster_id='{cluster_id}'",
             "--parameter", "fetchAccounts=true"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            self.owner_cache[cluster_id] = ("", "")
            return ("", "")

        try:
            data = json.loads(result.stdout)
            items = data.get("items", [])
            if not items:
                self.owner_cache[cluster_id] = ("", "")
                return ("", "")
            creator = items[0].get("creator", {})
            name = creator.get("name", "") or \
                f"{creator.get('first_name', '')} {creator.get('last_name', '')}".strip()
            email = get_original_email_address(creator.get("email", ""))
            self.owner_cache[cluster_id] = (name, email)
            return (name, email)
        except (json.JSONDecodeError, KeyError, IndexError):
            self.owner_cache[cluster_id] = ("", "")
            return ("", "")

    def enrich_with_ocm(self, traces):
        if self.skip_ocm:
            return

        # Group clusters by OCM account so we login once per account
        by_account = defaultdict(dict)
        for trace in traces:
            if trace.cluster_type in ("unknown", "prow-ci"):
                continue
            ocm_entry = (
                self.ocm_clusters.get(trace.cluster_name)
                or self.ocm_clusters.get(trace.cluster_id)
            )
            if ocm_entry:
                trace.ocm_account = ocm_entry["ocm_account"]
                cid = ocm_entry["id"]
                trace.cluster_id = cid
                account = ocm_entry["ocm_account"]
                if cid not in by_account[account]:
                    by_account[account][cid] = []
                by_account[account][cid].append(trace)

        for account, cluster_map in by_account.items():
            if not self._ocm_login(account):
                continue
            for cluster_id, group_traces in cluster_map.items():
                owner_name, owner_email = self.lookup_cluster_owner(cluster_id, account)
                for t in group_traces:
                    t.owner_name = owner_name
                    t.owner_email = owner_email

    # ── Prunability ────────────────────────────────────────────────

    def classify_prunability(self, traces, age_threshold=30):
        now = datetime.now(timezone.utc)
        managed_types = {"rosa-hcp", "rosa-classic", "osd", "osd-hcp", "ipi"}

        for trace in traces:
            age_days = self._age_days(trace.launch_time, now)

            if trace.cluster_type == "prow-ci":
                if trace.is_expired:
                    trace.prunability = "prunable"
                elif not trace.expiration_date:
                    trace.prunability = "questionable"
                else:
                    trace.prunability = "not-prunable"

            elif trace.cluster_type in managed_types:
                ocm_entry = (
                    self.ocm_clusters.get(trace.cluster_name)
                    or self.ocm_clusters.get(trace.cluster_id)
                )
                ocm_status = ocm_entry["status"] if ocm_entry else ""
                if ocm_status == "error":
                    trace.prunability = "questionable"
                elif age_days >= age_threshold:
                    trace.prunability = "questionable"
                else:
                    trace.prunability = "not-prunable"

            elif trace.cluster_type == "unknown":
                if age_days >= age_threshold:
                    trace.prunability = "questionable"
                else:
                    trace.prunability = "not-prunable"

            else:
                trace.prunability = "not-prunable"

    def _age_days(self, launch_time_str, now):
        if not launch_time_str:
            return 0
        try:
            dt = datetime.fromisoformat(launch_time_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (now - dt).days
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def filter_traces(traces, filter_value):
        if not filter_value:
            return traces
        allowed = set(filter_value.split("+"))
        return [t for t in traces if t.prunability in allowed]

    # ── Orchestration ─────────────────────────────────────────────

    def trace(self, resource_ids=None, states=None, scan=False):
        if scan:
            state_list = states or ["running", "stopped"]
            print(f"Scanning {self.region} for instances in states: {', '.join(state_list)}")
            instances = self.fetch_ec2_instances(states=state_list)
        elif resource_ids:
            instances = self.fetch_ec2_instances(instance_ids=resource_ids)
        else:
            print("Error: provide instance IDs or use --scan")
            sys.exit(1)

        print(f"Found {len(instances)} instances")

        traces = [self.classify_ec2_instance(i) for i in instances]

        if not self.skip_ocm:
            print("Loading OCM cluster data...")
            self.load_ocm_clusters()
            print(f"Loaded {len(self.ocm_clusters) // 2} clusters from OCM")
            self.enrich_with_ocm(traces)

        return traces

    # ── Grouping ──────────────────────────────────────────────────

    def group_by_cluster(self, traces):
        groups = defaultdict(list)
        for t in traces:
            key = (t.cluster_name or t.resource_id, t.cluster_type)
            groups[key].append(t)

        type_order = {
            "rosa-hcp": 0, "rosa-classic": 1, "osd": 2, "osd-hcp": 3,
            "ipi": 4, "prow-ci": 5, "unknown": 6,
        }
        return sorted(groups.items(), key=lambda x: (type_order.get(x[0][1], 99), x[0][0]))

    # ── Text report ───────────────────────────────────────────────

    def format_text_report(self, traces):
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = []
        lines.append(f"AWS Resource Tracer — {self.region} (EC2 Instances)")
        lines.append("=" * 60)
        lines.append(f"Scanned: {len(traces)} instances | {now_str}")
        lines.append("")

        grouped = self.group_by_cluster(traces)

        for (cluster_name, cluster_type), group in grouped:
            sample = group[0]

            if cluster_type == "unknown":
                label = "Unassociated"
            else:
                label = f"{cluster_name} ({_type_label(cluster_type)})"

            prunability_tag = _prunability_tag(group)
            if prunability_tag:
                label += f" {prunability_tag}"

            lines.append(f"── {label} " + "─" * max(1, 58 - len(label)))

            if sample.owner_name or sample.owner_email:
                owner = sample.owner_name
                if sample.owner_email:
                    owner += f" <{sample.owner_email}>"
                ocm = f" | OCM: {sample.ocm_account}" if sample.ocm_account else ""
                lines.append(f"   Owner: {owner}{ocm}")

            if cluster_type == "prow-ci" and sample.prow_job:
                lines.append(f"   Job: {sample.prow_job}")
                lines.append(f"   Build: {sample.prow_build_id}")

            if sample.expiration_date:
                exp_status = f"expired {sample.expired_ago} ago" if sample.is_expired else "active"
                lines.append(f"   Expiration: {sample.expiration_date} ({exp_status})")

            if sample.cluster_id:
                lines.append(f"   Cluster ID: {sample.cluster_id}")

            lines.append("")
            lines.append(
                f"   {'INSTANCE':<24} {'TYPE':<14} {'STATE':<10} {'LAUNCHED':<13} {'AGE':<8} {'NAME'}"
            )
            for t in sorted(group, key=lambda x: x.resource_id):
                launched = t.launch_time[:10] if t.launch_time else ""
                name_col = t.name if cluster_type == "unknown" else ""
                lines.append(
                    f"   {t.resource_id:<24} {t.instance_type:<14} {t.state:<10} {launched:<13} {t.age_str:<8} {name_col}"
                )
            lines.append("")

        lines.append(_format_summary(traces, grouped))
        return "\n".join(lines)

    def format_json_report(self, traces):
        return json.dumps([asdict(t) for t in traces], indent=2, default=str)


# ── Utilities ─────────────────────────────────────────────────────

def get_original_email_address(email):
    if not email or "+" not in email.split("@")[0]:
        return email
    local, domain = email.split("@")
    original_local = local.split("+")[0]
    return f"{original_local}@{domain}"


def _get_ocm_token():
    import os
    return os.environ.get("OCM_TOKEN", "")


def _prunability_tag(group):
    prunabilities = {t.prunability for t in group if t.prunability}
    if not prunabilities:
        return ""
    if "prunable" in prunabilities:
        return "[PRUNABLE]"
    if "questionable" in prunabilities:
        return "[QUESTIONABLE]"
    return ""


def _type_label(cluster_type):
    labels = {
        "rosa-hcp": "ROSA HCP",
        "rosa-classic": "ROSA Classic",
        "osd": "OSD",
        "osd-hcp": "OSD HCP",
        "ipi": "IPI",
        "prow-ci": "Prow CI",
        "unknown": "Unknown",
    }
    return labels.get(cluster_type, cluster_type)


def _format_summary(traces, grouped):
    lines = ["=" * 60, "Summary", "=" * 60]

    type_counts = defaultdict(lambda: {"instances": 0, "clusters": set(), "expired_clusters": set()})
    for (cluster_name, cluster_type), group in grouped:
        tc = type_counts[cluster_type]
        tc["instances"] += len(group)
        tc["clusters"].add(cluster_name)
        if any(t.is_expired for t in group):
            tc["expired_clusters"].add(cluster_name)

    lines.append(f"Total: {len(traces)} instances, {sum(len(tc['clusters']) for tc in type_counts.values())} clusters")
    lines.append("")

    for ct in ["rosa-hcp", "rosa-classic", "osd", "osd-hcp", "ipi", "prow-ci", "unknown"]:
        tc = type_counts.get(ct)
        if not tc or tc["instances"] == 0:
            continue
        expired_note = ""
        if tc["expired_clusters"]:
            expired_note = f", {len(tc['expired_clusters'])} expired"
        lines.append(
            f"  {_type_label(ct):<15} {tc['instances']:>4} instances  "
            f"({len(tc['clusters'])} clusters{expired_note})"
        )

    prunable_count = sum(1 for t in traces if t.prunability == "prunable")
    questionable_count = sum(1 for t in traces if t.prunability == "questionable")
    if prunable_count or questionable_count:
        lines.append("")
        lines.append("Prunability:")
        if prunable_count:
            lines.append(f"  Prunable:        {prunable_count:>4} instances")
        if questionable_count:
            lines.append(f"  Questionable:    {questionable_count:>4} instances")
        not_prunable = len(traces) - prunable_count - questionable_count
        lines.append(f"  Not prunable:    {not_prunable:>4} instances")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="AWS Resource Tracer — trace resources to clusters and owners (read-only)"
    )
    parser.add_argument(
        "resource_ids", nargs="*",
        help="Resource IDs to trace (e.g., EC2 instance IDs)"
    )
    parser.add_argument(
        "--region", default="us-east-1",
        help="AWS region (default: us-east-1)"
    )
    parser.add_argument(
        "--resource-type", default="ec2", choices=["ec2"],
        help="Resource type to trace (default: ec2)"
    )
    parser.add_argument(
        "--scan", action="store_true",
        help="Scan all resources of the given type in the region"
    )
    parser.add_argument(
        "--states", default="running,stopped",
        help="Comma-separated instance states for --scan (default: running,stopped)"
    )
    parser.add_argument(
        "--ocm-accounts", default="PROD,STAGE",
        help="Comma-separated OCM accounts to query (default: PROD,STAGE)"
    )
    parser.add_argument(
        "--skip-ocm", action="store_true",
        help="Skip OCM lookups"
    )
    parser.add_argument(
        "--output", default="text", choices=["text", "json"],
        help="Output format (default: text)"
    )
    parser.add_argument(
        "--filter", default=None,
        choices=["prunable", "questionable", "prunable+questionable", "not-prunable"],
        help="Filter output by prunability category"
    )
    parser.add_argument(
        "--age-threshold", type=int, default=30,
        help="Days after which a resource is considered questionably prunable (default: 30)"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not args.scan and not args.resource_ids:
        print("Error: provide resource IDs or use --scan")
        sys.exit(1)

    tracer = ResourceTracer(
        region=args.region,
        ocm_accounts=args.ocm_accounts.split(","),
        skip_ocm=args.skip_ocm,
    )

    traces = tracer.trace(
        resource_ids=args.resource_ids or None,
        states=args.states.split(",") if args.scan else None,
        scan=args.scan,
    )

    tracer.classify_prunability(traces, age_threshold=args.age_threshold)
    traces = tracer.filter_traces(traces, args.filter)

    if args.output == "json":
        print(tracer.format_json_report(traces))
    else:
        print(tracer.format_text_report(traces))


if __name__ == "__main__":
    main()
