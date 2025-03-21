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

    # Handle push events
    if "head_commit" in data and data.get("ref"):
        return handle_push_event(data)

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

def handle_push_event(data):
    """
    Handle a push event by computing the delta of file changes and updating agent knowledge.
    
    The delta is computed by combining:
      - Added and modified files (as "modified")
      - Removed files (as "deleted")
      - For renamed files, the new filename is considered modified, and the previous filename is considered deleted.
    """
    try:
        repo = get_repo_info(data)

        # Only process pushes to the onboarded target branch.
        if data.get("ref") != f"refs/heads/{repo['branch']}":
            msg = f"Push not to onboarded branch: {repo['branch']}; ignoring."
            logger.info(msg)
            return jsonify({"status": "ignored", "reason": msg}), 200

        head = data.get("head_commit", {})
        delta = compute_delta(head, repo)

        event = {
            "event_type": "repo-update",
            "meta_data": delta,
        }

        result = update_agent_knowledge(repo, event)
        logger.info(f"Push event processed for repo: {repo['url']}")
        return jsonify({"status": "processed push", "result": result}), 200

    except Exception as e:
        logger.exception("Error processing push event")
        return jsonify({"status": "error", "error": str(e)}), 500
    
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
    
def compute_delta(head_commit, repo):
    """
    Compute the delta for a GitHub push event based on the head_commit data.
    
    The delta is returned as a dictionary with:
      - "modified": Files that were added, modified, or renamed (new filenames)
      - "deleted": Files that were removed or renamed (previous filenames)
    
    Only files within the repo's src_folder and ending with '.py' are considered.
    
    Args:
        head_commit (dict): The 'head_commit' payload from the push event.
        repo (dict): Repository configuration with a 'src_folder' key.
    
    Returns:
        dict: A dictionary with keys "modified" and "deleted".
    """
    added_files = head_commit.get("added", [])
    modified_files = head_commit.get("modified", [])
    removed_files = head_commit.get("removed", [])
    renamed_files = head_commit.get("renamed", [])  # may be dicts or strings

    modified = []
    deleted = []

    def is_valid_file(filepath):
        return filepath.startswith(repo["src_folder"])

    # Process added and modified files.
    for f in added_files:
        if is_valid_file(f):
            modified.append(f)
    for f in modified_files:
        if is_valid_file(f):
            modified.append(f)

    # Process removed files.
    for f in removed_files:
        if is_valid_file(f):
            deleted.append(f)

    # Process renamed files.
    for item in renamed_files:
        if isinstance(item, dict):
            new_filename = item.get("filename", "")
            prev_filename = item.get("previous_filename", "")
            if is_valid_file(new_filename):
                modified.append(new_filename)
            if prev_filename and is_valid_file(prev_filename):
                deleted.append(prev_filename)
        else:
            if is_valid_file(item):
                modified.append(item)

    return {"modified": modified, "deleted": deleted}

@app.route('/repositories', methods=['GET'])
def get_repositories():
    """
    Endpoint to fetch all onboarded repositories.
    Returns a list of RepoRecord objects.
    """
    try:
        store = get_store("sqlite", db_path="store.db")
        repos = store.get_all_repos()
        return jsonify([{
            "url": repo.url,
            "src_folder": repo.src_path,
            "branch": repo.branch
        } for repo in repos]), 200
    except Exception as e:
        logger.exception("Error fetching repositories")
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