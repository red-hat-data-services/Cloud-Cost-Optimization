# Cloud-Cost-Optimization
# Summary<a id="docs-internal-guid-716a7cd6-7fff-2310-c0e1-6784ac966e3c"></a>

This document describes various ideas, opportunities and best practices to optimize the cloud cost for Red Hat Openshift AI. It also explains in detail about the design and implementation of the infrastructure to implement this plan. At the end it summarizes the output and the cost saving we achieved for Red Hat Openshift AI using this infrastructure.


# Cost Optimization Plan

- Each cluster has **inactive durations** when cluster is not getting used, but it is still running and increasing the cloud cost

- We should **hibernate** the clusters or **freeze** its major **cloud resources** during inactive period to save the cloud cost

- Possible inactive durations

  - **Weekend** - All clusters should be hibernated each weekend and resumed back when the week starts. It can achieve up to **25% cost saving** per month

  - **Daily** - Each cluster has some inactive hours each day, ( which can be confirmed by the cluster owner). Cluster can be hibernated during these inactive hours and can result into **\~50% cost saving** per month

- Hibernation and resume both should be **automated** to:

  - Save manual efforts

  - Make it happen consistently without failure

  - Avoid disruption to cluster users

- Users should be able to resume a cluster manually whenever needed

- OSD and ROSA-Classic clusters have a standard hibernation support

- **ROSA-Hosted (Hypershift) and IPI clusters** do not support hibernation, we should develop a **custom hibernation infrastructure** for these type of clusters


# Implementation completed

1. **Auditing and review of existing clusters** along with guiding the team to select cost-optimized cluster for their use case using the[ **Cluster Selection Guide**](https://docs.google.com/document/d/15uNbi-iPpyollSOCf8akRSo0KkmxpuTwjqKcz1e7v_g/edit#heading=h.nxuzyaoe35dq)

2. **Hibernation Infrastructure** design and development

   1. [**Hypershift Hibernation**](https://docs.google.com/document/d/1LCAfVPY_OEupehuP6exTL01YxnLAvJ7ptCtwQRx9QWU/edit#heading=h.axq5a0z70t0c) ****infrastructure - to hibernate and resume the HCP clusters

   2. [**IPI Hibernation**](https://docs.google.com/document/d/1LCAfVPY_OEupehuP6exTL01YxnLAvJ7ptCtwQRx9QWU/edit#heading=h.jc4a1jim91th) ****infrastructure - to hibernate and resume the IPI clusters

   3. [**Automated Weekend Hibernation**](https://github.com/red-hat-data-services/Cloud-Cost-Optimization/actions/workflows/hibernate_clusters_weeend.yaml) infrastructure - to automatically hibernate all the clusters each weekend and resume when the week starts

   4. [**Automated Daily Hibernation**](https://github.com/red-hat-data-services/Cloud-Cost-Optimization/actions/workflows/hibernate_clusters_daily.yaml) Infrastructure - to automatically hibernate and resume all the clusters based on the inactive hours provided by the cluster owner

   5. [**Cluster Stats Smartsheet**](https://app.smartsheet.com/sheets/3XQ2Xg8pjCxpH7qX6wJG7Qv2R6MJ7GWM8HHF4wf1?view=grid) - An auto-populated smartsheet which is always latest with the details of all the RHOAI clusters from PROD and STAGE accounts, it is also used to configure the inactive hours for daily hibernation

   6. [**On-Demand Hibernation / Resume**](https://github.com/red-hat-data-services/Cloud-Cost-Optimization/actions/workflows/hibernate_cluster.yaml) infrastructure - to enable team members to hibernate or resume their clusters whenever they need

3. [**Automated Cloud Cleanup**](https://github.com/red-hat-data-services/Cloud-Cost-Optimization/actions/workflows/cloud_cleaner.yaml) infrastructure - to regularly cleanup the leftover cloud resources from deleted openshift clusters

4. [**Best Practices**](https://docs.google.com/document/d/1LCAfVPY_OEupehuP6exTL01YxnLAvJ7ptCtwQRx9QWU/edit#heading=h.lapggmvohbpv) Formulation - Devised and documented team-wide best practices to save the cloud cost along with educating the team about the same


# Best Practices

- Before creating a new cluster, please refer the [Cluster Selection Guide](https://docs.google.com/document/d/15uNbi-iPpyollSOCf8akRSo0KkmxpuTwjqKcz1e7v_g/edit#heading=h.nxuzyaoe35dq) to identify the least expensive cluster for your use case

- Please ensure to [update the “**Inactive Hours**”](https://docs.google.com/document/d/1LCAfVPY_OEupehuP6exTL01YxnLAvJ7ptCtwQRx9QWU/edit#heading=h.psyj3fvvt8v) for your cluster to ensure **Automated Daily Hibernation**

- There is an **Automated Weekend Hibernation** for all the clusters, please use DevOps infra to [resume the cluster if needed in between](https://docs.google.com/document/d/1LCAfVPY_OEupehuP6exTL01YxnLAvJ7ptCtwQRx9QWU/edit#heading=h.z9m5h0yuehvy)

- Please [hibernate your personal OSD or ROSA clusters](https://docs.google.com/document/d/1LCAfVPY_OEupehuP6exTL01YxnLAvJ7ptCtwQRx9QWU/edit#heading=h.bs1iic89i58f) before going on any long vacation

Make sure to register your disconnected clusters to OCM, follow [this doc](https://access.redhat.com/documentation/en-us/openshift_cluster_manager/2023/html/managing_clusters/assembly-cluster-subscriptions#registering-disconnected-ocp-clusters_assembly-cluster-subscriptions) for detailed steps


# FAQs<a id="docs-internal-guid-a1d38b04-7fff-8247-e09e-bfefd50ef8f4"></a>

## How to update the inactive hours for your cluster

1. Open the [RHOAI Clusters](https://app.smartsheet.com/sheets/3XQ2Xg8pjCxpH7qX6wJG7Qv2R6MJ7GWM8HHF4wf1?view=grid) smartsheet

2. If login screen is shown, then select the “Sign in with Google”, provide your RH email and follow the single sign on 

3. Find your cluster in the list

4. Update “**Inactive Hours - Start (UTC)**” and “**Inactive Hours - End (UTC)**” columns for your cluster

5. It has to be provided as per the **UTC** timezone

6. It has to be **HH:MM:SS** as per the 24 hours time format (**without** any AM or PM) 

7. If the Inactive hours are left empty, then the cluster will not be hibernated or resumed.


## How ROSA-Hosted clusters are hibernated

- ROSA-Hosted clusters do not support hibernation as a standard feature

- We have devised a custom hibernation strategy to switch-off the corresponding EC2 instances and delete the root EBS volumes to save the cost

- We have designed and developed the [tooling / infrastructure using python and github-actions](https://github.com/red-hat-data-services/Cloud-Cost-Optimization/actions/workflows/hibernate_cluster.yaml) to implement our custom hibernation strategy


## How IPI clusters are hibernated

- IPI clusters do not support hibernation as a standard feature

- We have devised a custom hibernation strategy to switch-off the corresponding EC2 instances to save the cost

- We have designed and developed the [tooling / infrastructure using python and github-actions](https://github.com/red-hat-data-services/Cloud-Cost-Optimization/actions/workflows/hibernate_cluster.yaml) to implement our custom hibernation strategy


## How to manually hibernate a ROSA-Hosted or IPI cluster

1. Go to [Hibernate Cluster](https://github.com/red-hat-data-services/Cloud-Cost-Optimization/actions/workflows/hibernate_cluster.yaml) github action

2. Click on “Run Workflow” button on right side of the page

3. Provide the “Cluster Name” and select correct “OCM Account”

4. Click “Run Workflow”

**PS** - This workflow can hibernate an OSD cluster as well, but it will not wait for the hibernation to complete.


## How to manually hibernate an OSD cluster

1. Login to the respective OCM account from where the cluster is created

2. Go to “Clusters” page, locate your cluster in the list

3. Click on 3 dots in front of the cluster, and Click “Hibernate”


## How to manually resume a ROSA-Hosted or IPI cluster

1. Go to [Resume Cluster](https://github.com/red-hat-data-services/Cloud-Cost-Optimization/actions/workflows/resume_cluster.yaml) github action

2. Click on “Run Workflow” button on right side of the page

3. Provide the “Cluster Name” and select correct “OCM Account”

4. Click “Run Workflow”

**PS** - This workflow can resume an OSD cluster as well, but it will not wait for the resumption to complete.


## How to manually resume an OSD cluster

1. Login to the respective OCM account from where the cluster is created

2. Go to “Clusters” page, locate your cluster in the list

3. Click on 3 dots in front of the cluster, and Click “Resume from Hibernation”


## How to check the status of your cluster

1. Open the [RHOAI Clusters](https://app.smartsheet.com/sheets/3XQ2Xg8pjCxpH7qX6wJG7Qv2R6MJ7GWM8HHF4wf1?view=grid) smartsheet

2. If login screen is shown, then select the “Sign in with Google”, provide your RH email and follow the single sign on 

3. Find your cluster in the list and check the status
