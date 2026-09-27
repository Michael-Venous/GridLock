"""Claude Opus 5 on Amazon Bedrock (the Converse API), for the ingest agent.

Credentials come from the standard AWS chain (AWS_PROFILE, SSO, environment); nothing is stored in the repo. The
model and region can be changed with GRIDLOCK_BEDROCK_MODEL and AWS_REGION. Needs boto3 (requirements-agent.txt);
the rest of the pipeline doesn't.
"""
import os
import re
import sys

# tried in order; an account that can't use one (no access, not offered in the region, a request shape it rejects)
# moves on to the next
MODELS = [os.environ["GRIDLOCK_BEDROCK_MODEL"]] if os.environ.get("GRIDLOCK_BEDROCK_MODEL") else ["us.anthropic.claude-opus-5"]
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
CACHE = {"cachePoint": {"type": "default"}}
# turn endings whose tool calls can't be trusted (cut off, or not well formed), and endings that end the conversation
CUT_OFF = ("max_tokens", "malformed_tool_use", "malformed_model_output")
STOPPED = ("content_filtered", "guardrail_intervened", "model_context_window_exceeded")


class Session:
    """One conversation's client and running token count."""

    def __init__(self, model=None):
        try:
            import boto3
            from botocore.config import Config
        except ImportError:
            raise SystemExit("The ingest agent needs boto3: pip install -r requirements-agent.txt")
        self.models = [model] if model else list(MODELS)
        self.model = self.models[0]
        self.client = boto3.client("bedrock-runtime", region_name=REGION, config=Config(
            read_timeout=900, connect_timeout=30, retries={"max_attempts": 10, "mode": "adaptive"}))
        self.usage = {"inputTokens": 0, "outputTokens": 0, "cacheReadInputTokens": 0, "cacheWriteInputTokens": 0, "calls": 0}

    def unusable(self, e):
        """Whether an error says this model can't be used here, rather than that the request is wrong: no access, or a
        validation or not-found error that names the model or the tool choice."""
        ex = self.client.exceptions
        if isinstance(e, ex.AccessDeniedException):
            return True
        return isinstance(e, (ex.ValidationException, ex.ResourceNotFoundException)) and bool(
            re.search(rf"{re.escape(self.model)}|model id|model identifier|tool_?choice", str(e), re.I))

    def converse(self, system, messages, tools=None, max_tokens=32000):
        while True:
            # Explicit Converse cache points are supported by our Claude path, not GPT-6.
            # Preserve all other blocks, including reasoning signatures and tool results.
            cache = [CACHE] if "anthropic." in self.model else []
            clean_messages = [{**m, "content": [b for b in m["content"] if "cachePoint" not in b]}
                              for m in messages] if not cache else messages
            kw = {"system": [{"text": system}] + cache, "messages": clean_messages,
                  "inferenceConfig": {"maxTokens": max_tokens}}
            if tools:
                kw["toolConfig"] = {"tools": [{"toolSpec": t} for t in tools] + cache}
            # Use provider-default reasoning; do not send a provider-specific field.
            try:
                resp = self.client.converse(modelId=self.model, **kw)
                break
            except Exception as e:
                if self.model == self.models[-1] or not self.unusable(e):
                    raise
                nxt = self.models[self.models.index(self.model) + 1]
                print(f"  can't use {self.model} ({str(e)[:120]}...); using {nxt}", file=sys.stderr)
                self.model = nxt
        for k, v in resp.get("usage", {}).items():
            if k in self.usage:
                self.usage[k] += v
        self.usage["calls"] += 1
        return resp

    def force_tool(self, system, text, tool, check=None, tries=3):
        """Structured output: the model answers by calling this one tool; returns its input. The tool choice stays auto
        (Claude Opus 5.5 rejects a forced one), so the prompt asks for the call, and a turn without it, or with input
        that check(input) finds problems in, is answered with what was wrong and asked again."""
        messages = [{"role": "user", "content": [{"text": f"{text}\n\nAnswer by calling the {tool['name']} tool."}]}]
        why = []
        for _ in range(tries):
            resp = self.converse(system, messages, tools=[tool], max_tokens=16000)
            msg = resp["output"]["message"]
            uses = [b["toolUse"] for b in msg["content"] if "toolUse" in b]
            use = next((u for u in uses if u["name"] == tool["name"]), None)
            if resp.get("stopReason") in STOPPED:
                break
            if resp.get("stopReason") in CUT_OFF:
                why = [f"your answer was cut off ({resp['stopReason']}); answer again, more briefly"]
            elif use is None:
                why = [f"you did not call the {tool['name']} tool"]
            else:
                why = check(use["input"]) if check else []
                if not why:
                    return use["input"]
            messages.append(msg)
            again = f"Answer by calling the {tool['name']} tool; " + "; ".join(why)
            messages.append({"role": "user", "content": [{"toolResult": {"toolUseId": u["toolUseId"], "content": [{"text": again}], "status": "error"}}
                                                         for u in uses] or [{"text": again}]})
        raise RuntimeError(f"model did not answer with a valid {tool['name']} call ({resp.get('stopReason')}): {'; '.join(why)[:300]}")
