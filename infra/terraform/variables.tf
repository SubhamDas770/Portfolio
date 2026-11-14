variable "vercel_project_name" {
  description = "Name of the Vercel project to create."
  type        = string
  default     = "subham-das-portfolio"
}

variable "vercel_function_region" {
  description = "Vercel Function region nearest to the Neon project."
  type        = string
  default     = "sin1"
}

variable "neon_project_name" {
  description = "Name of the Neon Postgres project to create."
  type        = string
  default     = "subham-das-portfolio"
}

variable "neon_region_id" {
  description = "Neon AWS region identifier. The project region cannot be changed after creation."
  type        = string
  default     = "aws-ap-southeast-1"
}
