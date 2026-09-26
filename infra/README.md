# Alert service (AWS)

Emails planners when a new DESC or Georgia filing changes projects or pairs in an area they drew in the Changes tab. It's optional: without it the Changes tab still works, and the sign-up form says alerts are off.

## What runs

| Piece | AWS | What it does |
|---|---|---|
| `backend/worker.py` | Lambda, daily at 7:00 New York time (EventBridge Scheduler) | Checks SCRTP's home page for a new DESC list and GA PSC docket 56002 for a new annual transmission update. On a new filing: reruns the pipeline, stores the results in S3, emails each confirmed subscriber the changes in their area. |
| `backend/api.py` | Lambda function URL | `POST /subscribe`, `POST /sample`, `GET /status` for the Changes tab. |
| Subscribers | DynamoDB (on demand) | Email, drawn area, distance, kinds of change. The id is an HMAC of the email. |
| Email | SNS topic with one email subscription per subscriber | SNS sends the confirmation and adds an unsubscribe link to every message. A filter policy on each subscription means a publish reaches only its subscriber. |
| Data | S3 bucket (private) | The filing registry, change log, caches and raw PDFs the worker reads and writes. |

Both functions run one container image (`backend/Dockerfile`: Python 3.12 + poppler, the pipeline and the handlers).

## Deploy

Needs the AWS CLI v2, Docker (running), Terraform 1.6 or newer and Python 3.

```bash
sudo systemctl start docker                       # if Docker is off
aws configure sso --profile gridlock              # or: aws configure --profile gridlock
AWS_PROFILE=gridlock GRIDLOCK_ACCOUNT_ID=123456789012 infra/deploy.sh
```

`GRIDLOCK_ACCOUNT_ID` must be the account the profile points at; the script and Terraform both refuse any other account. Optional environment variables: `MAINTAINER_EMAIL` (who hears about filings the pipeline can't read), `BUDGET_EMAIL` (a $5/month budget that emails once spending passes $1), `APP_URL` (linked from emails), `EXTRA_ORIGIN` (a hosted site's origin, allowed to call the API).

The script creates the registry, builds and pushes the image, applies Terraform, seeds the bucket from `data/` the first time, and writes the API address into `data/alerts.json`. Rerun it to ship a new image; it never overwrites the registry the worker keeps in S3.

## Try it

1. Serve the app (`python3 -m http.server 8000`), open the Changes tab, draw an area and sign up.
2. Confirm the email from AWS Notifications.
3. Press "Send me a sample". It is built from the newest real filing that touches your area and labelled as a sample.
4. To run the watcher by hand: `aws lambda invoke --function-name gridlock-worker --profile gridlock out.json && cat out.json`. With no new filing it answers `{"new": []}`.

## Limits and costs

- Real alerts fire only when a utility publishes a new edition, about once a year each.
- Georgia's update has been filed in the IRP docket (56002). A new IRP opens a new docket, which has to be added to `watch.ga.dockets` in `data/filings.json`.
- If a new filing can't be downloaded or parsed, the maintainers get the error and subscribers get only "a new filing was posted" with the link.
- The sign-up URL is public. Inputs are validated, each email can sign up five times a day, and samples go only to confirmed subscribers, one every ten minutes.
- At hackathon scale this stays inside the free tier: a few Lambda runs a day, a handful of DynamoDB items, SNS email (1,000 free a month), a small S3 bucket and one image in ECR.

`terraform destroy` (with the same `-var` values the script passes) removes everything, including the bucket and its contents.
