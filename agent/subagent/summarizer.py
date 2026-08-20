from google.adk.agents import Agent
import os

model_name = os.getenv("MODEL")
summarizer = Agent(
    name='summarizer',
    model = model_name,
)