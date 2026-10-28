variable "floci_endpoint" {
  # 127.0.0.1, not localhost: on Windows + Docker Desktop, "localhost" adds a
  # fixed ~43 ms to every request (docs/m0-findings.md, E4).
  description = "URL of the Floci AWS emulator."
  type        = string
  default     = "http://127.0.0.1:4566"
}

variable "fixture_mode" {
  description = "Bridge fixture mode: live, record, replay or replay_or_record (README FR-11)."
  type        = string
  default     = "live"

  validation {
    condition     = contains(["live", "record", "replay", "replay_or_record"], var.fixture_mode)
    error_message = "fixture_mode must be live, record, replay or replay_or_record."
  }
}

variable "contact_email" {
  description = "Sent to NCBI as the contact email (fills $${OPENRX_CONTACT_EMAIL}). Set it in terraform.tfvars, which is not committed; empty sends none."
  type        = string
  default     = ""
}
