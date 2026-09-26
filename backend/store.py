"""The AWS side, kept thin so the rest can be tested without an account.

S3 (BUCKET)   state/filings.json, state/changes.json, state/projects.json, state/env/*, state/cache/*; raw/<pdf>
DynamoDB      one table: subscribers (id = HMAC of the email) and short-lived rate counters (id "rate#...", TTL)
SNS           ALERTS topic: one email subscription per subscriber, filter policy {"subscriber": [id]}, so a
              publish tagged with that id reaches only that person. SNS handles confirmation and unsubscribe.
              MAINTAINERS topic: the people who fix the parsers when a new filing won't parse.
"""
import hashlib
import hmac
import json
import os
import time


class Store:
    def __init__(self, s3, ddb, sns, bucket, table, topic, maintainers=None, secret="", app_url=None):
        self.s3, self.ddb, self.sns = s3, ddb, sns
        self.bucket, self.table, self.topic, self.maintainers = bucket, table, topic, maintainers
        self.secret, self.app_url = secret.encode(), app_url

    @classmethod
    def from_env(cls):
        import boto3   # present in the Lambda runtime; tests pass fakes instead
        return cls(boto3.client("s3"), boto3.client("dynamodb"), boto3.client("sns"), os.environ["BUCKET"], os.environ["TABLE"],
                   os.environ["TOPIC_ARN"], os.environ.get("MAINTAINERS_ARN") or None, os.environ["ID_SECRET"], os.environ.get("APP_URL") or None)

    # ---- S3 ----
    def get_json(self, key, default=None):
        try:
            return json.loads(self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())
        except self.s3.exceptions.NoSuchKey:
            return default

    def put_json(self, key, obj):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(obj, separators=(",", ":")).encode(), ContentType="application/json")

    def download(self, key, path):
        self.s3.download_file(self.bucket, key, str(path))

    def upload(self, path, key, content_type="application/octet-stream"):
        self.s3.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type})

    def keys(self, prefix):
        out, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix, **({"ContinuationToken": token} if token else {})}
            page = self.s3.list_objects_v2(**kw)
            out += [o["Key"] for o in page.get("Contents", [])]
            token = page.get("NextContinuationToken")
            if not token:
                return out

    # ---- subscribers ----
    def subscriber_id(self, email):
        return hmac.new(self.secret, email.strip().lower().encode(), hashlib.sha256).hexdigest()[:24]

    def get_subscriber(self, sid):
        item = self.ddb.get_item(TableName=self.table, Key={"id": {"S": sid}}).get("Item")
        return json.loads(item["doc"]["S"]) if item else None

    def put_subscriber(self, sub):
        self.ddb.put_item(TableName=self.table, Item={"id": {"S": sub["id"]}, "doc": {"S": json.dumps(sub)}})

    def subscribers(self):
        out, key = [], None
        while True:
            kw = {"TableName": self.table, **({"ExclusiveStartKey": key} if key else {})}
            page = self.ddb.scan(**kw)
            out += [json.loads(i["doc"]["S"]) for i in page.get("Items", []) if not i["id"]["S"].startswith("rate#")]
            key = page.get("LastEvaluatedKey")
            if not key:
                return out

    def bump(self, key, limit, ttl_s):
        """Count one use of key; False once it passes limit within ttl_s. Atomic, so it holds under concurrency."""
        now = int(time.time())
        try:
            self.ddb.update_item(TableName=self.table, Key={"id": {"S": f"rate#{key}"}},
                                 UpdateExpression="ADD n :one SET expires = if_not_exists(expires, :exp)",
                                 ConditionExpression="attribute_not_exists(n) OR n < :limit",
                                 ExpressionAttributeValues={":one": {"N": "1"}, ":exp": {"N": str(now + ttl_s)}, ":limit": {"N": str(limit)}})
            return True
        except self.ddb.exceptions.ConditionalCheckFailedException:
            return False

    # ---- SNS ----
    def subscribe(self, email, sid):
        r = self.sns.subscribe(TopicArn=self.topic, Protocol="email", Endpoint=email, ReturnSubscriptionArn=True,
                               Attributes={"FilterPolicy": json.dumps({"subscriber": [sid]})})
        return r["SubscriptionArn"]

    def confirmed(self, arn):
        if not arn or not arn.startswith("arn:"):
            return False
        try:
            return self.sns.get_subscription_attributes(SubscriptionArn=arn)["Attributes"].get("PendingConfirmation") == "false"
        except self.sns.exceptions.NotFoundException:
            return False

    def publish(self, sid, subject, body):
        self.sns.publish(TopicArn=self.topic, Subject=subject, Message=body,
                         MessageAttributes={"subscriber": {"DataType": "String", "StringValue": sid}})

    def tell_maintainers(self, subject, body):
        if self.maintainers:
            self.sns.publish(TopicArn=self.maintainers, Subject=subject[:100], Message=body)
