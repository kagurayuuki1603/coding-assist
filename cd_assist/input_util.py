def should_exit(user_input):
    return user_input.lower() in {"exit", "quit", "bye"}

def check_input(user_input, keyword):
    return user_input.lower() == keyword or user_input.lower().startswith(f"{keyword} ")

def should_explain(user_input):
    return check_input(user_input, "explain")

def should_ask(user_input):
    return check_input(user_input, "ask")

def should_interpret(user_input):
    return check_input(user_input, "interpret")

def should_select_tool(user_input):
    return check_input(user_input, "select")

def should_retrieve_tool(user_input):
    return check_input(user_input, "retrieve")

def should_find_bugs(user_input):
    return check_input(user_input, "find bugs")