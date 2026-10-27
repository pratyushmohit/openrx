variable "floci_endpoint" {
  # 127.0.0.1, not localhost: on Windows + Docker Desktop, "localhost" adds a
  # fixed ~43 ms to every request (docs/m0-findings.md, E4).
  description = "URL of the Floci AWS emulator."
  type        = string
  default     = "http://127.0.0.1:4566"
}
