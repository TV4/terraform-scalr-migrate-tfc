# TV4 Migration from TFC to Scalr
This repository contains a Python script which was used to migrate approximately 1000 workspaces including variables from Terraform Cloud to Scalr in roughly two hours. It also updates the CDK for Terraform code for all of the workspaces to switch backend to Scalr.

## Differences from [Scalr/terraform-scalr-migrate-tfc](https://github.com/Scalr/terraform-scalr-migrate-tfc)
We chose to fork and modify the excellent base repository from Scalr, introducing the following changes:

- Migration is now done by executing a Python script instead of a Terraform module. This made it easier for us to debug the migration and felt more natural to us.
- Selecting workspaces to migrate is now done with a full regex instead of a wildcard.
- Refactored the code to be more in the style of what we are used to reading, which ended up being a class-based structured.
- Migrating sensitive Terraform variables using variable backups that we stored in AWS SSM.
- Injecting TypeScript code to redirect all of our CDK for Terraform projects to the Scalr backend without requiring updates to our internal CDKTF wrapper package which configures the Terraform backend.

## Potentially TV4-specific assumptions
The script in this repository makes a few assumptions that are specific to the way that Terraform is handled at TV4. In particular,

- All secrets are backed up in AWS SSM with parameter names like `{prefix}/{workspace}/{variable}`. This is used to be able to migrate the values of sensitive variables, which cannot be done natively as the Terraform Cloud APIs only expose values for non-sensitive variables.
- AWS authentication uses profiles
- The CDKTF code for all workspaces is present in a monorepo with the following structure where Terraform workspaces are named e.g. `projecta-envname-accountlabel` and `accountlabel` has a one-to-one mapping with Scalr environments. 

```
cdktf-path
└── projects
    ├── projectA
    │   ├── cdktf.out
    │   │   └── stacks
    │   │       └── envName-accountLabel
    │   │           └── cdk.tf.json
    │   └── main.ts
    └── projectB
        ├── cdktf.out
        │   └── stacks
        │       └── envNameB-accountLabel
        │           └── cdk.tf.json
        └── main.ts
```

- All workspaces are on Terraform 1.5.7 or lower. If not, check the state file to ensure that `version` is 4 -- if it is, you can simply rewrite the `terraform-version` field in the state file to 1.5.7 and upload the state to a 1.5.7 workspace as the statefile format has not changed. This was the case for us, but we manually reviewed the workspaces that this applied to.
- The main script for the CDKTF code is called `main.ts` and contains a CDKTF `App` called `app` which has a `app.synth()` call.


## Known issues
### Missing pagination
The methods for listing secrets do not handle pagination. This needed to be remedied manually by running additional ad-hoc scripts, and should probably be fixed before this is used by other organizations.

### Migration performance
While we were able to migrate 1000 workspaces in 2 hours which was sufficiently fast for us to not focus more on performance optimization, the script is naïvely synchronous and would be orders of magnitude faster if rewritten to use multithreading. This would probably be worthwhile if a larger number of workspaces are to be migrated.


## Usage
```
$ python main.py --help
usage: main.py [-h] --workspace-regex WORKSPACE_REGEX --cdktf-path CDKTF_PATH --scalr-account-id SCALR_ACCOUNT_ID --vcs-id VCS_ID --scalr-hostname SCALR_HOSTNAME
               [--tf-hostname TF_HOSTNAME] --tf-organization TF_ORGANIZATION [--aws-profile AWS_PROFILE] [--aws-region AWS_REGION] --aws-ssm-prefix AWS_SSM_PREFIX

options:
  -h, --help            show this help message and exit
  --workspace-regex WORKSPACE_REGEX
                        Regex to match workspaces to migrate
  --cdktf-path CDKTF_PATH
                        Path to the CDKTF repository
  --scalr-account-id SCALR_ACCOUNT_ID
                        Scalr account ID
  --vcs-id VCS_ID       Scalr VCS ID
  --scalr-hostname SCALR_HOSTNAME
                        Scalr hostname
  --tf-hostname TF_HOSTNAME
                        Terraform Cloud hostname
  --tf-organization TF_ORGANIZATION
                        Terraform Cloud organization
  --aws-profile AWS_PROFILE
                        AWS profile to use for fetching secrets from SSM. Defaults to the AWS_PROFILE environment variable.
  --aws-region AWS_REGION
                        AWS region to use for fetching secrets from SSM. Defaults to the AWS_REGION environment variable.
  --aws-ssm-prefix AWS_SSM_PREFIX
                        AWS SSM parameter prefix. Secrets are assumed to be stored under {prefix}/{workspace_name}/{key}
```

Dependencies are listed in `requirements.txt` and can be installed with `pip install -r requirements.txt` (preferably in a virtual environment). The migration was executed with Python 3.12.1, but older versions should work fine.