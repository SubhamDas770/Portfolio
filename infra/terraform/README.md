# Vercel and Neon infrastructure

Terraform creates a Vercel project configured for serverless Functions and a
Neon Postgres project. The default regions are Singapore (`sin1` on Vercel and
`aws-ap-southeast-1` on Neon). Neon regions are immutable after project
creation; change `neon_region_id` before the first apply if another region is
preferred.

The Neon project is configured for the Free plan's published limits: compute
is capped at 0.25 CU, auto-suspends after five minutes, and the Terraform quota
caps active compute time and logical database size. These provider-level caps
are intended to keep the project within the published free allocation; they
cannot guarantee uninterrupted service if the provider changes its limits.
Check the current Neon and Vercel pricing and account terms before applying.

## Prerequisites

- Terraform 1.14 or newer.
- A Vercel account/API token and Neon account/API key.
- Node.js/npm for the Vercel CLI.
- Docker Desktop for the separate local container workflow.

The Vercel provider is published as `vercel/vercel`. The Neon provider used
here is published in the `kislerdm/neon` namespace, rather than the
`neondatabase` namespace. Review the provider publisher and the checked-in
`.terraform.lock.hcl` checksums before initializing or upgrading providers.

Set provider credentials in the current PowerShell session without adding
them to a command string or file:

```powershell
function Set-SecretEnvironmentVariable([string]$Name) {
  $secret = Read-Host $Name -AsSecureString
  $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
  try {
    Set-Item "Env:$Name" ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer))
  }
  finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
  }
}

Set-SecretEnvironmentVariable VERCEL_API_TOKEN
Set-SecretEnvironmentVariable NEON_API_KEY
```

## Review and apply infrastructure

Run from the project root. The helper stores Terraform state under the current
Windows user's local application data directory rather than the OneDrive
workspace; the Neon provider's state includes the database password and
connection URI:

```powershell
.\infra\terraform\init.ps1
Set-Location infra\terraform
terraform fmt -check -recursive
terraform validate
terraform plan
```

Review the full plan and confirm the account, region, and free-tier limits
before applying:

```powershell
terraform apply
```

The Neon provider resource returns a database password and connection URI.
Terraform marks these fields sensitive in normal output, but they are still
stored in Terraform state. The helper writes that file to
`%LOCALAPPDATA%\SubhamPortfolio\Terraform\terraform.tfstate`, outside the
OneDrive workspace. Keep it private on this machine, never commit or upload it,
and do not publish state/output files. The repository ignores local state and
variables. Do not configure Vercel environment values as Terraform resources;
this avoids adding application secrets to further state attributes. Back up
state only to a protected, encrypted location because losing it can leave
resources unmanaged and compromise Terraform destroy operations.

## Connect the Git repository and deploy

Terraform creates the Vercel project but does not connect a Git repository.
After applying, open the `subham-das-portfolio` project in the Vercel dashboard
and connect the GitHub repository
[`SubhamDas770/Portfolio`](https://github.com/SubhamDas770/Portfolio) under
**Settings > Git**. Set the production branch to `main` and leave the project
root directory at the repository root, where `vercel.json` and
`requirements.txt` are located.

Before the first deployment, add the following under **Settings > Environment
Variables**, selecting **Production** only:

- `POSTGRES_URL`: Neon connection string. Retrieve it from Neon; do not put it
  in Terraform output, source files, or Git.
- `OPENROUTER_API_KEY`: a newly rotated key.
- `ADMIN_PASSWORD`: a unique, strong dashboard password.
- `ADMIN_SESSION_SECRET`: a random secret of at least 32 characters.
- `CRON_SECRET`: a separate random secret for scheduled tasks.
- `GITHUB_USERNAME`: `SubhamDas770`.

`GITHUB_TOKEN` is optional. `OPENROUTER_MODEL` is also optional and defaults to
`openai/gpt-4.1-mini`. Do not add production database credentials to Preview
deployments unless those deployments are intentionally allowed to write to the
production database.

After the first deployment, add `PUBLIC_SITE_URL` as the HTTPS origin of the
production site and redeploy so the setting takes effect. Once the repository
is connected, pushes to `main` create production deployments automatically;
other branches create Preview deployments according to the project's Vercel
Git settings. Review those settings before sharing preview URLs.

Use a newly rotated OpenRouter key, a unique dashboard password, and
independent random session and cron secrets. Provider usage limits and
OpenRouter inference charges still apply.

Vercel Hobby and Neon Free have usage limits. OpenRouter inference may incur
separate usage charges. Configure any provider-side spending limits before
making the public chat available.

## Destroying infrastructure

Terraform destroy permanently removes the Neon project and its data, as well
as the Vercel project. Back up any data first, then inspect the destroy plan:

```powershell
terraform plan -destroy
terraform destroy
```
