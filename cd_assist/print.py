COMPUTER = "╰┈➤ˎˊ˗ "
CONFUSED = "∘ ∘ ∘ ( °ヮ° ) ? "
GAMBA = "(•̀ᴗ•́ )و "
YAHO = "(˶ᵔᗜᵔ˶)ﾉﾞ"
EXCITE = "₍₍⚞(˶>ᗜ<˶)⚟⁾⁾"

def print_intro(workspace):
    print(f"{COMPUTER}Hello! I'm your coding assistant for {workspace} {EXCITE}")

def print_goodbye():
    print(f"{COMPUTER}Goodbye! {YAHO}")

def print_no_file():
    print(f"{COMPUTER}{CONFUSED} No file path provided.")

def print_no_query():
    print(f"{COMPUTER}{CONFUSED} No query provided.")

def print_exception(error):
    print(f"{COMPUTER}{CONFUSED} Error encountered: {error}")

def print_interpretation(interpretation):
    print(f"{COMPUTER}{GAMBA} I have interpreted!")
    print(f"Intent: {interpretation.intent.value}")
    print(f"Target: {interpretation.target or 'Not specified'}")
    print("Search terms:")

    for term in interpretation.search_terms:
        print(f"- {term}")

def print_agent_response(response):
    print(f"{COMPUTER}{EXCITE} I have a response!")
    print(response)


def print_idk():
    print(f"{COMPUTER}{CONFUSED}")