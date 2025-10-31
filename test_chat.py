from langchain_google_genai import ChatGoogleGenerativeAI
import dotenv

dotenv.load_dotenv()

llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash")

# Simple text invocation
result = llm.invoke("What is the capital of Finland?")
print(result.content)