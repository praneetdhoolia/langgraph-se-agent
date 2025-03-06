import logging
import os

from flask import Flask, request, jsonify
from flask_cors import CORS

from se_agent.integration.langgraph_runtime import (
    apply_agent,
    update_agent_knowledge,
)
from se_agent.store import (
    get_store,
    RepoRecord
)
from se_agent.utils.utils_git_api import (
    post_issue_comment
)

app = Flask(__name__)
CORS(app)
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

SE_AGENT_USER_ID = "heurisdev"

@app.route('/onboard', methods=['POST', 'PUT'])
def onboard():
    """
    Endpoint for repo onboarding.
    Expects JSON with:
      - repo_url (e.g., "https://github.com/owner/repo")
      - src_folder (path in the repo containing code)
      - branch (optional, defaults to "main")
    """
    data = request.json
    try:
        repo = {
            "url": data["repo_url"],
            "src_folder": data["src_folder"],
            "branch": data.get("branch", "main"),
        }
    except KeyError as e:
        error_msg = f"Missing required field: {str(e)}"
        logger.error(error_msg)
        return jsonify({"status": "error", "error": error_msg}), 400

    # Create a "repo-onboard" event with empty meta_data.
    event = {
        "event_type": "repo-onboard",
        "meta_data": {},
    }

    try:
        result = update_agent_knowledge(repo, event)
        logger.info(f"Repo onboarded: {repo['url']}")
        return jsonify({"status": "onboarded", "result": result}), 200
    except Exception as e:
        logger.exception("Error during onboarding")
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    General GitHub webhook endpoint.
    Dispatches to the correct handler based on payload content:
      - Issue creation events (action == "opened" with an "issue")
    """
    data = request.json

    # Handle issues and issue comments.
    if "issue" in data:
        action = data.get("action")
        # Only process newly opened issues.
        if action != "opened":
            logger.info(f"Issue event with action '{action}' ignored.")
            return jsonify({"status": "ignored", "reason": f"Action '{action}' not supported"}), 200
        return handle_issue_event(data)

    logger.info("Received unsupported webhook event.")
    return jsonify({"status": "ignored", "reason": "Event type not supported"}), 200

def handle_issue_event(data):
    try:
        repo = get_repo_info(data)
        issue = data.get("issue")
        issue_title = issue.get("title", "")
        issue_description = issue.get("body", "")
        combined_text = f"{issue_title}\n{issue_description}"
        if ignore_if_not_mentioned(combined_text, "issue"):
            return jsonify({"status": "ignored", "reason": "Agent not mentioned"}), 200

        messages = [{"role": "user", "content": combined_text}]
        token = get_github_token()
        result = apply_agent_and_respond(messages, repo, issue.get("number"), token)
        logger.info(f"Issue processed for repo: {repo['url']}")
        return jsonify({"status": "processed issue", "result": result}), 200
    except Exception as e:
        logger.exception("Error processing issue creation event")
        return jsonify({"status": "error", "error": str(e)}), 500

def get_repo_info(data):
    repo_url = data.get("repository", {}).get("html_url")
    if not repo_url:
        raise ValueError("Repository URL not found in webhook data.")
    store = get_store("sqlite", db_path="store.db")
    repo_record: RepoRecord = store.get_repo(repo_url)
    if not repo_record:
        raise ValueError(f"Repository {repo_url} not onboarded.")
    return {
        "url": repo_url,
        "src_folder": repo_record.src_path,
        "branch": repo_record.branch,
    }

def get_github_token():
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN not set")
    return token

def should_process_event(text: str) -> bool:
    """
    Returns True if the event text contains a mention for the agent.
    """
    if not text:
        return False
    # Using lower() makes the check case-insensitive.
    return SE_AGENT_USER_ID in text.lower()

def ignore_if_not_mentioned(text, context):
    if not should_process_event(text):
        logger.info(f"Agent not mentioned in {context}; ignoring event.")
        return True
    return False

def extract_agent_response(result):
    """
    Generic function to extract the AI message from the agent's response.
    Assumes the last message in the result is the agent response if its type is 'ai'.
    """
    messages = result.get("messages", [])
    if messages:
        last_message = messages[-1]
        if last_message.get("type") == "ai":
            return last_message.get("content")
    return None

def apply_agent_and_respond(messages, repo, issue_number, token):
    """
    Applies the agent with the given messages and repo info, extracts the AI response,
    and posts it back as a comment on the GitHub issue.
    """
    result = apply_agent(messages, repo)
    agent_response = extract_agent_response(result)
    if agent_response:
        # Extract the URL from the repo dict
        post_issue_comment(repo['url'], issue_number, agent_response, gh_token=token)
    else:
        logger.error(f"Unexpected agent response: {result}")
    return result

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)