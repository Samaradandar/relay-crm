# Rotating secrets

| Secret | Where it lives | How to rotate |
| --- | --- | --- |
| Database passwords (`relay`, `relay_app`) | AWS Secrets Manager | Rotate in Secrets Manager, then redeploy API and worker. |
| `DEV_JWT_SECRET` | local only | Never used in staging or production (the app refuses `ENV=production` with the dev provider). |
| Cognito user pool / client | Terraform (`infra/modules/auth`) | Create a new app client, switch `COGNITO_CLIENT_ID`, delete the old one. |
| S3 / SES access | IAM task role | No static keys on AWS. Locally MinIO uses `S3_ACCESS_KEY` / `S3_SECRET_KEY`. |

Never put secrets in the repo, in audit payloads or in logs. After rotating, check `GET /health`
and sign in with one account per role.
