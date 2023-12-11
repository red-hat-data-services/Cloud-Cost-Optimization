# Hypershift Cluster Hibernation



# Summary

This document describes the possible design and implementation of a method to hibernate Hypershift (HCP or ROSA-Hosted) clusters.


# Context

- HCP clusters do not have a hibernation support yet

- An active HCP cluster uses multiple cloud resources in backend

- Cumulative cost of these cloud resource turns to be the cloud cost to host the HCP cluster

- Top 2 cloud resources to contribute to the cluster cost are:

  - EC2 instances

  - EBS Volumes




# Hibernation Design & Implementation

- Find all the running EC2 instances for a given HCP cluster

- The corresponding EC2 instances should be stopped (NOT terminated)

- Find all the root EBS volumes attached to the stopped instances (not the additional volumes)

- Delete the root EBS volumes

- This would save the cost of EC2 instances and EBS volumes for the duration of hibernation

- This design is implemented here:

  - [Hibernate Cluster Workflow](https://github.com/red-hat-data-services/Cloud-Cost-Optimization/actions/workflows/hibernate_cluster.yaml)

  - [Backend Implementation](https://github.com/red-hat-data-services/Cloud-Cost-Optimization/blob/main/src/hibernate_cluster.py)


# Resume Design & Implementation

- Find all the stopped EC2 instances for a given HCP cluster

- The corresponding EC2 instances should be terminated

- As soon as instances are terminated, cluster machine pools will automatically create the new EC2 instances along with new root EBS volumes

- As soon as the instances are up and running, the cluster will be back to the active running state

- This design is implemented here:

  - [Resume Cluster Workflow](https://github.com/red-hat-data-services/Cloud-Cost-Optimization/actions/workflows/resume_cluster.yaml)

  - [Backend Implementation](https://github.com/red-hat-data-services/Cloud-Cost-Optimization/blob/main/src/resume_cluster.py)
