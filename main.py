from argparse import ArgumentParser
import json
import os

from migrator import Migrator


def parse_args():
    parser = ArgumentParser()
    parser.add_argument(
        "--workspace-regex",
        required=True,
        help="Regex to match workspaces to migrate",
        type=str,
    )
    parser.add_argument(
        "--cdktf-path",
        required=True,
        help="Path to the CDKTF repository",
        type=str,
    )
    parser.add_argument(
        "--scalr-account-id",
        required=True,
        help="Scalr account ID",
        type=str,
    )
    parser.add_argument(
        "--vcs-id",
        required=True,
        help="Scalr VCS ID",
        type=str,
    )
    parser.add_argument(
        "--scalr-hostname",
        required=True,
        help="Scalr hostname",
        type=str,
    )
    parser.add_argument(
        "--tf-hostname",
        help="Terraform Cloud hostname",
        default="app.terraform.io",
        type=str,
    )
    parser.add_argument(
        "--tf-organization",
        required=True,
        help="Terraform Cloud organization",
        type=str,
    )
    parser.add_argument(
        "--aws-profile",
        required=False,
        help="AWS profile to use for fetching secrets from SSM. Defaults to the AWS_PROFILE environment variable.",
        type=str,
        default=os.getenv("AWS_PROFILE", "missing"),
    )
    parser.add_argument(
        "--aws-region",
        required=False,
        help="AWS region to use for fetching secrets from SSM. Defaults to the AWS_REGION environment variable.",
        default=os.getenv("AWS_REGION", "missing"),
    )
    parser.add_argument(
        "--aws-ssm-prefix",
        type=str,
        required=True,
        help="AWS SSM parameter prefix. Secrets are assumed to be stored under {prefix}/{workspace_name}/{key}",
    )

    args = parser.parse_args()
    return args


def main():
    args = parse_args()
    tf_organization = args.tf_organization
    scalr_account_id = args.scalr_account_id
    vcs_id = args.vcs_id
    scalr_hostname = args.scalr_hostname
    tf_hostname = args.tf_hostname
    cdktf_path = args.cdktf_path
    workspace_regex = args.workspace_regex
    aws_profile = args.aws_profile
    aws_region = args.aws_region

    with open(os.path.expanduser("~/.terraform.d/credentials.tfrc.json"), "r") as f:
        credentials = json.load(f)
    tfe_token = credentials["credentials"][tf_hostname]["token"]
    scalr_token = credentials["credentials"][scalr_hostname]["token"]

    migrator = Migrator(
        lock=True,
        scalr_token=scalr_token,
        tf_token=tfe_token,
        tf_hostname=tf_hostname,
        scalr_hostname=scalr_hostname,
        tf_organization=tf_organization,
        account_id=scalr_account_id,
        skip_workspace_creation=False,
        skip_backend_secrets=False,
        cdktf_path=cdktf_path,
        workspace_regex=workspace_regex,
        vcs_id=vcs_id,
        aws_profile=aws_profile,
        aws_region=aws_region,
    )
    migrator.migrate_workspaces()


if __name__ == "__main__":
    main()
