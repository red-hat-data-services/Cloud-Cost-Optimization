# IPI Cluster Hibernation



# Summary

This document describes the possible design and implementation of a method to hibernate IPI (ocp) clusters.


# Context

- IPI clusters do not have a hibernation support yet

- An active IPI cluster uses multiple cloud resources in backend

- Cumulative cost of these cloud resource turns to be the cloud cost to host the IPI cluster

- Top cloud resource to contribute to the cluster cost is:

  - EC2 instances



# Hibernation Design & Implementation

- Find all the running EC2 instances for a given IPI cluster

- The corresponding EC2 instances should be stopped (NOT terminated)

* This would save the cost of EC2 instances for the duration of hibernation

- This design is implemented here:

  - [Hibernate Cluster Workflow](https://github.com/red-hat-data-services/Cloud-Cost-Optimization/actions/workflows/hibernate_cluster.yaml)

  - [Backend Implementation](https://github.com/red-hat-data-services/Cloud-Cost-Optimization/blob/main/src/hibernate_cluster.py)


# Resume Design & Implementation

- Find all the stopped EC2 instances for a given IPI cluster

- The corresponding EC2 instances should be started

* As soon as the instances are up and running, the cluster will be back to the active running state

- This design is implemented here:

  - [Resume Cluster Workflow](https://github.com/red-hat-data-services/Cloud-Cost-Optimization/actions/workflows/resume_cluster.yaml)

  - [Backend Implementation](https://github.com/red-hat-data-services/Cloud-Cost-Optimization/blob/main/src/resume_cluster.py)
