from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from se_agent.config import Configuration
from se_agent.state import PRState
from se_agent.utils.utils_misc import is_context_limit_error, load_chat_model
from se_agent.utils.utils_git_api import get_pr_diff, get_pr_files, get_file_content_from_github


async def review_pull_request(state: PRState, *, config: RunnableConfig) -> dict:
    configuration = Configuration.from_runnable_config(config)

    pr_title = state.pr_event["pull_request"]["title"]
    pr_description = state.pr_event["pull_request"]["body"]
    pr_author = state.pr_event["pull_request"]["user"]["login"]
    pr_number = state.pr_event["pull_request"]["number"]
    pr_diff = get_pr_diff(repo_url=state.repo.url, pr_number=pr_number, gh_token=configuration.gh_token)

    # Get the files involved in the PR
    pr_files = get_pr_files(repo_url=state.repo.url, pr_number=pr_number, gh_token=configuration.gh_token)
    
    # Fetch the content of each file and create the code_files string
    code_files = []
    src_folder = state.repo.src_folder
    for file in pr_files:
        if file["status"] in ["added", "modified", "renamed"] and file["filename"].startswith(src_folder):
            file_content = get_file_content_from_github(
                repo_url=state.repo.url,
                filepath=file["filename"],
                gh_token=configuration.gh_token,
                branch=f"refs/pull/{pr_number}/head"
            )
            file_extension = file["filename"].split('.')[-1]
            code_files.append(f"```{file_extension}\n{file_content}\n```")
    
    code_files_str = "\n\n## Code for relevant files\n\n" + '\n\n'.join(code_files)

    template = ChatPromptTemplate.from_messages([
        ("human", configuration.pull_request_review_system_prompt)
    ])
    model = load_chat_model(configuration.pull_request_review_model)
    context = template.invoke({
        "pr_title": pr_title,
        "pr_description": pr_description,
        "pr_author": pr_author,
        "pr_diff": pr_diff,
        "code_files": code_files_str,
        "test_framework": configuration.test_framework,
    }, config)
    try:
        response = await model.ainvoke(context, config)
    except Exception as e:
        if is_context_limit_error(e):
            # Create an alternate prompt without code_files
            context = template.invoke({
                "pr_title": pr_title,
                "pr_description": pr_description,
                "pr_author": pr_author,
                "pr_diff": pr_diff,
                "code_files": "",
                "test_framework": configuration.test_framework,
            }, config)
            response = await model.ainvoke(context, config)
        else:
            raise e
    return {
        "messages": [response]
    }


# Initialize the state with default values
builder = StateGraph(state_schema=PRState, config_schema=Configuration)

builder.add_node(review_pull_request)

builder.add_edge(START, "review_pull_request")
builder.add_edge("review_pull_request", END)

graph = builder.compile()

graph.name = "ReviewPRGraph"
