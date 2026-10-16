# TFLint config, run in CI (terraform-lint job) and locally with:
#   tflint --init && tflint --format compact
plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

# Not in the recommended preset; every input and output gets a description
# so the terraform-docs tables in README.md are complete.
rule "terraform_documented_variables" {
  enabled = true
}

rule "terraform_documented_outputs" {
  enabled = true
}

plugin "azurerm" {
  enabled = true
  version = "0.32.0"
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}

# prevent_destroy can't be driven by a variable, so it would block the
# `terraform destroy` of throwaway dev/test environments.
rule "azurerm_resources_missing_prevent_destroy" {
  enabled = false
}
