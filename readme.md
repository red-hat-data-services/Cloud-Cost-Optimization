# Running the pruner script

```
usage: ./pruner.sh [-j] [-p] [-s] [JOB_ID_1 JOB_ID_2 ...]
  -j, --show-old-jobs - Print old job names and exit
  -p, --project - Specify google cloud project id
  -s, --service-account - Specify google cloud service account (need to specify a key file as well)
  -k, --key-file - Specify key file for google cloud service account
  JOB_IDs - delete resources for specific OpenShift CI job IDs only
```

# Determining what resources are created by OpenShift CI

- Go to openshift ci and select a job (for example: https://prow.ci.openshift.org/job-history/gs/test-platform-results/pr-logs/directory/pull-ci-opendatahub-io-opendatahub-operator-main-opendatahub-operator-e2e and select a job)
- Click on "Artifacts" near the top and it should open up an apache-like file browser
- You can either download all the artifacts or navigate to the given path in the browser:
  ```
  artifacts/<name of ci>/ipi-install-install/artifacts/.openshift-install-XXX.log
  ```
- The log contains the output of a `terraform apply`, which we can use to figure out what GCP resources are created.
- Can use sed to cleanup the log file
  ```
  cat openshift-install.log | sed -E 's/^time=.* level=[a-zA-Z]+ *(.*)/\1/' | sed -E 's/^msg="(.*)"$/\1/' > openshift-install-cleaned.log
  ```
- and can grep out the lines where things are created
  ```
  > grep 'will be created' openshift-install-cleaned.log
    # module.dns.google_dns_managed_zone.int[0] will be created
    # module.dns.google_dns_record_set.api_external[0] will be created
    # module.dns.google_dns_record_set.api_external_internal_zone will be created
    # module.dns.google_dns_record_set.api_internal will be created
    # module.iam.google_project_iam_member.worker-compute-viewer will be created
    # module.iam.google_project_iam_member.worker-storage-admin will be created
    # module.iam.google_service_account.worker-node-sa will be created
    # module.master.google_compute_instance.master[0] will be created
    # module.master.google_compute_instance.master[1] will be created
    # module.master.google_compute_instance.master[2] will be created
    # module.master.google_compute_instance_group.master[0] will be created
    # module.master.google_compute_instance_group.master[1] will be created
    # module.master.google_compute_instance_group.master[2] will be created
  ... etc
  ```
- An example created resource from the log file:
  ```
  # module.dns.google_dns_managed_zone.int[0] will be created
    + resource \"google_dns_managed_zone\" \"int\" {
        + creation_time   = (known after apply)
        + description     = \"Created By OpenShift Installer\"
        + dns_name        = \"ci-op-xyz123ab-xyzab.XXXXXXXXXXXXXXXXXXXXXXXXX.\"
        + force_destroy   = false
        + id              = (known after apply)
        + labels          = {
            + \"kubernetes-io-cluster-ci-op-xyz123ab-xyzab-abc12\" = \"owned\"
          }
        + managed_zone_id = (known after apply)
        + name            = \"ci-op-xyz123ab-xyzab-abc12-private-zone\"
        + name_servers    = (known after apply)
        + project         = \"XXXXXXXXXXXXXXXXXX\"
        + visibility      = \"private\"      + cloud_logging_config {
            + enable_logging = (known after apply)
          }      + private_visibility_config {          + networks {
                + network_url = (known after apply)
              }
          }
      } 
  ```
- This creates a dns managed zone with the given name. since it's the first thing in the terraform output and is the first thing created, we also know that it should probably be the last the last thing we should delete.


Relevant docs:

https://steps.ci.openshift.org/reference/ipi-install-install

