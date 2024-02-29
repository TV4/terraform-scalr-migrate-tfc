import binascii
import hashlib
import json
import requests
from urllib.parse import urlencode
import boto3
from glob import glob
from tqdm import tqdm


class Migrator:
    def __init__(
        self,
        scalr_hostname,
        scalr_token,
        tf_hostname,
        tf_token,
        tf_organization,
        account_id,
        vcs_id: str,
        workspace_regex,
        skip_workspace_creation,
        skip_backend_secrets,
        lock,
        cdktf_path,
        aws_profile,
        aws_region,
        aws_ssm_prefix,
    ):
        self.scalr_hostname = scalr_hostname
        self.scalr_token = scalr_token
        self.tf_hostname = tf_hostname
        self.tf_token = tf_token
        self.tf_organization = tf_organization
        self.account_id = account_id
        self.workspace_wildcard = workspace_regex
        self.skip_workspace_creation = skip_workspace_creation
        self.skip_backend_secrets = skip_backend_secrets
        self.lock = lock
        self.boto_session = boto3.Session(
            region_name=aws_region, profile_name=aws_profile
        )
        self.vcs_id = vcs_id
        self.ssm = self.boto_session.client("ssm")
        self.environments = self.fetch_environments()
        self.cdktf_paths = self.find_cdktf_paths(cdktf_path)
        self.migrated_cdktf_mains = set()
        self.ssm_prefix = aws_ssm_prefix

    def find_cdktf_paths(self, cdktf_path: str):
        directories = glob(f"{cdktf_path}/projects/*")
        paths = {}
        for directory in directories:
            project_name = directory.split("/")[-1].lower()
            paths[project_name] = directory
        return paths

    def _get_tfc_headers(self):
        return {
            "Authorization": f"Bearer {self.tf_token}",
            "Content-Type": "application/vnd.api+json",
        }

    def fetch_environments(self):
        environments: dict[str, str] = {}
        next = "environments"
        while True:
            response = self.fetch_scalr(next)
            for environment in response["data"]:
                environments[environment["attributes"]["name"]] = environment["id"]
            if response["links"]["next"]:
                next = response["links"]["next"].split("/")[-1]
            else:
                break
        return environments

    def _get_scalr_headers(self):
        return {
            "Authorization": f"Bearer {self.scalr_token}",
            "Prefer": "profile=preview",
            "Content-Type": "application/vnd.api+json",
        }

    def get_workspace_secrets(self, workspace_name: str) -> dict[str, str]:
        paginator = self.ssm.get_paginator("get_parameters_by_path")
        response_iterator = paginator.paginate(
            Path=f"{self.ssm_prefix}/{workspace_name}",
            WithDecryption=True,
            Recursive=True,
        )
        secrets = {}
        for page in response_iterator:
            for param in page["Parameters"]:
                secrets[param["Name"].split("/")[-1]] = param["Value"]
        return secrets

    def encode_filters(self, filters):
        encoded = ""
        if filters:
            encoded = f"?{urlencode(filters)}"
        return encoded

    def fetch_tfc(self, route, filters=None):
        req = f"https://{self.tf_hostname}/api/v2/{route}{self.encode_filters(filters)}"
        response = requests.get(req, headers=self._get_tfc_headers())

        if response.status_code not in [200]:
            raise Exception(
                f"Failed to fetch Scalr {req}\nResponse: {response.json()['errors'][0]}"
            )
        return response.json()

    def write_tfc(self, route, data):
        req = f"https://{self.tf_hostname}/api/v2/{route}"
        response = requests.post(
            req, headers=self._get_tfc_headers(), data=json.dumps(data)
        )

        if response.status_code not in [201, 200]:
            raise Exception(
                f"Failed to write to TFC {req}: {response.json()['errors'][0]}"
            )
        return response.json()

    def fetch_scalr(self, route, filters=None):
        req = f"https://{self.scalr_hostname}/api/iacp/v3/{route}{self.encode_filters(filters)}"
        response = requests.get(req, headers=self._get_scalr_headers())

        if response.status_code not in [200]:
            raise Exception(
                f"Failed to fetch Scalr {req}: {response.json()['errors'][0]}"
            )
        return response.json()

    def write_scalr(self, route, data):
        req = f"https://{self.scalr_hostname}/api/iacp/v3/{route}"
        response = requests.post(
            req,
            headers=self._get_scalr_headers(),
            json=data,
        )

        if response.status_code not in [201]:
            raise Exception(
                f"Failed to write to Scalr {req}:  {response.json()['errors'][0]}"
            )
        return response.json()

    def create_workspace(self, tf_workspace):
        attributes = tf_workspace["attributes"]
        name = attributes["name"]
        account = name.split("-")[-1]
        relationships = {
            "environment": {
                "data": {"type": "environments", "id": self.environments[account]}
            },
        }
        terraform_version = attributes["terraform-version"]
        if terraform_version > "1.5.7":
            raise Exception(f"Unsupported Terraform version: {terraform_version}")

        data = {
            "data": {
                "type": "workspaces",
                "attributes": {
                    "deletion-protection-enabled": True,
                    "name": name,
                    "auto-apply": attributes["auto-apply"],
                    "operations": attributes["operations"],
                    "terraform-version": attributes["terraform-version"],
                    "working-directory": attributes["working-directory"],
                },
            }
        }
        if attributes["vcs-repo"]:
            data["data"]["attributes"]["vcs-repo"] = {
                "identifier": attributes["vcs-repo"]["display-identifier"],
                "branch": "main",
                "dry-runs-enabled": False,
                "trigger-prefixes": [attributes["working-directory"]],
            }
            relationships["vcs-provider"] = {
                "data": {"type": "vcs-providers", "id": self.vcs_id}
            }
        data["data"]["relationships"] = relationships

        return self.write_scalr("workspaces", data)

    def create_state(self, tfc_state, workspace_id):
        attributes = tfc_state["attributes"]
        raw_state = requests.get(
            attributes["hosted-state-download-url"], headers=self._get_tfc_headers()
        )
        encoded_state = binascii.b2a_base64(raw_state.content)
        decoded = binascii.a2b_base64(encoded_state)
        state_version = {
            "data": {
                "type": "state-versions",
                "attributes": {
                    "serial": attributes["serial"],
                    "md5": hashlib.md5(decoded).hexdigest(),
                    "lineage": raw_state.json()["lineage"],
                    "state": encoded_state.decode("utf-8"),
                },
                "relationships": {
                    "workspace": {"data": {"type": "workspaces", "id": workspace_id}}
                },
            }
        }

        return self.write_scalr("state-versions", state_version)

    def create_variable(
        self,
        variable_key,
        value,
        category,
        sensitive,
        description=None,
        relationships=None,
    ):
        data = {
            "data": {
                "type": "vars",
                "attributes": {
                    "key": variable_key,
                    "value": value,
                    "category": category,
                    "sensitive": sensitive,
                    "description": description,
                },
                "relationships": relationships,
            }
        }

        self.write_scalr("vars", data)

    def lock_tfc_workspace(self, tf_workspace, workspace_name):
        if self.lock and not tf_workspace["attributes"]["locked"]:
            self.write_tfc(
                f"workspaces/{tf_workspace['id']}/actions/lock",
                {"reason": "Locked by migrator"},
            )

    def migrate_state(self, workspace, workspace_name):
        state_filters = {
            "filter[workspace][name]": workspace_name,
            "filter[organization][name]": self.tf_organization,
            "page[size]": 1,
        }
        for tf_state in self.fetch_tfc("state-versions", state_filters)["data"]:
            self.create_state(tf_state, workspace["id"])

    def migrate_variables(self, workspace, workspace_name):
        relationships = {
            "workspace": {"data": {"type": "workspaces", "id": workspace["id"]}}
        }

        vars_filters = {
            "filter[workspace][name]": workspace_name,
            "filter[organization][name]": self.tf_organization,
        }
        secrets = {}
        tfc_vars = self.fetch_tfc("vars", vars_filters)["data"]
        if any(variable["attributes"]["sensitive"] for variable in tfc_vars):
            secrets = self.get_workspace_secrets(workspace_name)
        for api_var in tfc_vars:
            attributes = api_var["attributes"]

            if not attributes["sensitive"]:
                self.create_variable(
                    attributes["key"],
                    attributes["value"],
                    attributes["category"],
                    False,
                    attributes["description"],
                    relationships,
                )
            else:
                name = attributes["key"]
                self.create_variable(
                    name,
                    secrets[name],
                    attributes["category"],
                    True,
                    attributes["description"],
                    relationships,
                )

    def migrate_workspaces(self):
        next_page = 1
        workspaces = []
        while True:
            workspace_filters = {
                "page[size]": 100,
                "page[number]": next_page,
                "search[wildcard-name]": self.workspace_wildcard,
            }

            tfc_workspaces = self.fetch_tfc(
                f"organizations/{self.tf_organization}/workspaces", workspace_filters
            )
            next_page = tfc_workspaces["meta"]["pagination"]["next-page"]
            workspaces.extend(tfc_workspaces["data"])
            if not next_page:
                break
        for tf_workspace in tqdm(workspaces, desc="Migrating workspaces"):
            terraform_version = tf_workspace["attributes"]["terraform-version"]
            if terraform_version.replace("~>", "") > "1.5.7":
                tqdm.write(
                    f"Skipping workspace {tf_workspace['attributes']['name']} with unsupported Terraform version {terraform_version}"
                )
                continue
            if tf_workspace["attributes"]["locked"]:
                continue
            if tf_workspace["attributes"]["resource-count"] == 0:
                tqdm.write(
                    f"Skipping workspace {tf_workspace['attributes']['name']} with no resources"
                )
                continue
            workspace_name = tf_workspace["attributes"]["name"]
            account = workspace_name.split("-")[-1]

            workspace_exists = self.fetch_scalr(
                "workspaces",
                {
                    "filter[name]": workspace_name,
                    "filter[environment]": self.environments[account],
                },
            )["data"]

            try:
                if workspace_exists:
                    tqdm.write(f"Workspace {workspace_name} already exists")
                    continue
                workspace = self.create_workspace(tf_workspace)["data"]
                self.migrate_state(workspace, workspace_name)
                self.migrate_variables(workspace, workspace_name)
                self.lock_tfc_workspace(tf_workspace, workspace_name)
                self.migrate_cdktf(workspace_name)
            except Exception as e:
                tqdm.write(f"Failed to migrate workspace {workspace_name}: {e}")

    def migrate_cdktf(self, workspace_name: str):
        js_environments = [f"{' '*8}{k}: '{v}'," for k, v in self.environments.items()]
        inject_js = f"""
// THIS IS A TEMPORARY INJECTION
app.node.children.forEach((stack) => {{
    const scalrEnvironments = {{
{'\n'.join(js_environments)}
    }}
    const infraStack = stack as InfraStack
    const environment = scalrEnvironments[infraStack.configuration.awsConfiguration.accountLabel]
    infraStack.addOverride('terraform.backend.remote.hostname', '{self.scalr_hostname}')
    infraStack.addOverride('terraform.backend.remote.organization', environment)
}})
// END OF TEMPORARY INJECTION
"""
        [projectName, envName, accountLabel] = workspace_name.split("-")
        if not projectName in self.migrated_cdktf_mains:
            with open(f"{self.cdktf_paths[projectName]}/main.ts", "r+") as f:
                content = f.readlines()
                try:
                    synth_index = content.index("app.synth()\n")
                except ValueError:
                    synth_index = content.index("app.synth()")
                content.insert(synth_index, inject_js)
                f.seek(0, 0)
                f.write("".join(content))
            self.migrated_cdktf_mains.add(projectName)

        with open(
            f"{self.cdktf_paths[projectName]}/cdktf.out/stacks/{envName}-{accountLabel}/cdk.tf.json",
            "r+",
        ) as f:
            cdktf = json.load(f)
            cdktf["terraform"]["backend"]["remote"]["hostname"] = self.scalr_hostname
            cdktf["terraform"]["backend"]["remote"]["organization"] = self.environments[
                accountLabel
            ]
            # synthing produces this comment for some reason, let's inject it to contain the diff
            cdktf["//"]["metadata"]["overrides"] = {"stack": ["terraform"]}
            f.seek(0, 0)
            f.write(json.dumps(cdktf, indent=2))
