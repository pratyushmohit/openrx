# API key for openFDA, read only by the bridge Lambda (docs/design.md FR-7).
#
# Terraform creates the secret *container* only. The value is set outside
# Terraform, because anything Terraform manages is written in plain text to
# its state file (docs/design.md NFR-3: no secrets in the repo or state).
resource "aws_secretsmanager_secret" "openfda_api_key" {
  name        = "openrx/openfda_api_key"
  description = "openFDA API key (optional; raises rate limits)."

  # Real AWS keeps deleted secrets for 7-30 days before purging them, which
  # blocks re-creating one with the same name. 0 deletes immediately, so
  # destroy/apply cycles work.
  recovery_window_in_days = 0
}

# API key for NCBI E-utilities (PubMed), read only by the bridge. Optional:
# without one NCBI allows 3 requests per second, with one 10.
resource "aws_secretsmanager_secret" "ncbi_api_key" {
  name                    = "openrx/ncbi_api_key"
  description             = "NCBI E-utilities API key (optional; raises rate limits)."
  recovery_window_in_days = 0
}
