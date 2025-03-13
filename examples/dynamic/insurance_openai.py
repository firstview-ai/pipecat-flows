#
# Copyright (c) 2024, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#
"""Insurance Quote Example using Pipecat Dynamic Flows.

This example demonstrates how to create a conversational insurance quote bot using:
- Dynamic flow management for flexible conversation paths
- LLM-driven function calls for consistent behavior
- Node configurations for different conversation states
- Pre/post actions for user feedback
- Transition logic based on user responses

The flow allows users to:
1. Provide their age
2. Specify marital status
3. Get an insurance quote
4. Adjust coverage options
5. Complete the quote process

Requirements:
- Daily room URL
- OpenAI API key
- Deepgram API key
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Dict, TypedDict, Union

import aiohttp
from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.deepgram import DeepgramSTTService, DeepgramTTSService
from pipecat.services.openai import OpenAILLMService
from pipecat.transports.services.daily import DailyParams, DailyTransport

from pipecat_flows import FlowArgs, FlowManager, FlowResult, NodeConfig

sys.path.append(str(Path(__file__).parent.parent))
from runner import configure

load_dotenv(override=True)

logger.remove(0)
logger.add(sys.stderr, level="DEBUG")


# Type definitions
class InsuranceQuote(TypedDict):
    monthly_premium: float
    coverage_amount: int
    deductible: int


class AgeCollectionResult(FlowResult):
    age: int


class MaritalStatusResult(FlowResult):
    marital_status: str


class QuoteCalculationResult(FlowResult, InsuranceQuote):
    pass


class CoverageUpdateResult(FlowResult, InsuranceQuote):
    pass


# Simulated insurance data
INSURANCE_RATES = {
    "young_single": {"base_rate": 150, "risk_multiplier": 1.5},
    "young_married": {"base_rate": 130, "risk_multiplier": 1.3},
    "adult_single": {"base_rate": 100, "risk_multiplier": 1.0},
    "adult_married": {"base_rate": 90, "risk_multiplier": 0.9},
}


# Function handlers
async def collect_age(args: FlowArgs) -> AgeCollectionResult:
    """Process age collection."""
    age = args["age"]
    logger.debug(f"collect_age handler executing with age: {age}")
    return AgeCollectionResult(age=age)


async def collect_marital_status(args: FlowArgs) -> MaritalStatusResult:
    """Process marital status collection."""
    status = args["marital_status"]
    logger.debug(f"collect_marital_status handler executing with status: {status}")
    return MaritalStatusResult(marital_status=status)


async def calculate_quote(args: FlowArgs) -> QuoteCalculationResult:
    """Calculate insurance quote based on age and marital status."""
    age = args["age"]
    marital_status = args["marital_status"]
    logger.debug(f"calculate_quote handler executing with age: {age}, status: {marital_status}")

    # Determine rate category
    age_category = "young" if age < 25 else "adult"
    rate_key = f"{age_category}_{marital_status}"
    rates = INSURANCE_RATES.get(rate_key, INSURANCE_RATES["adult_single"])

    # Calculate quote
    monthly_premium = rates["base_rate"] * rates["risk_multiplier"]

    return {
        "monthly_premium": monthly_premium,
        "coverage_amount": 250000,
        "deductible": 1000,
    }


async def update_coverage(args: FlowArgs) -> CoverageUpdateResult:
    """Update coverage options and recalculate premium."""
    coverage_amount = args["coverage_amount"]
    deductible = args["deductible"]
    logger.debug(
        f"update_coverage handler executing with amount: {coverage_amount}, deductible: {deductible}"
    )

    # Calculate adjusted quote
    monthly_premium = (coverage_amount / 250000) * 100
    if deductible > 1000:
        monthly_premium *= 0.9  # 10% discount for higher deductible

    return {
        "monthly_premium": monthly_premium,
        "coverage_amount": coverage_amount,
        "deductible": deductible,
    }


async def end_quote() -> FlowResult:
    """Handle quote completion."""
    logger.debug("end_quote handler executing")
    return {"status": "completed"}


# Transition callbacks and handlers
async def handle_age_collection(args: Dict, result: AgeCollectionResult, flow_manager: FlowManager):
    flow_manager.state["age"] = result["age"]
    await flow_manager.set_node("marital_status", create_marital_status_node())


async def handle_marital_status_collection(
    args: Dict, result: MaritalStatusResult, flow_manager: FlowManager
):
    flow_manager.state["marital_status"] = result["marital_status"]
    await flow_manager.set_node(
        "quote_calculation",
        create_quote_calculation_node(
            flow_manager.state["age"], flow_manager.state["marital_status"]
        ),
    )


async def handle_quote_calculation(
    args: Dict, result: QuoteCalculationResult, flow_manager: FlowManager
):
    await flow_manager.set_node("quote_results", create_quote_results_node(result))


async def handle_end_quote(_: Dict, result: FlowResult, flow_manager: FlowManager):
    await flow_manager.set_node("end", create_end_node())


# Node configurations
def create_initial_node() -> NodeConfig:
    """Create the initial node asking for age."""
    return {
        "role_messages": [
            {
                "role": "system",
                "content": (
                    "You are a friendly insurance agent. Your responses will be "
                    "converted to audio, so avoid special characters. Always use "
                    "the available functions to progress the conversation naturally."
                ),
            }
        ],
        "task_messages": [
            {
                "role": "system",
                "content": "Start by asking for the customer's age.",
            }
        ],
        "functions": [
            {
                "type": "function",
                "function": {
                    "name": "collect_age",
                    "handler": collect_age,
                    "description": "Record customer's age",
                    "parameters": {
                        "type": "object",
                        "properties": {"age": {"type": "integer"}},
                        "required": ["age"],
                    },
                    "transition_callback": handle_age_collection,
                },
            }
        ],
    }


def create_marital_status_node() -> NodeConfig:
    """Create node for collecting marital status."""
    return {
        "task_messages": [
            {
                "role": "system",
                "content": "Ask about the customer's marital status for premium calculation.",
            }
        ],
        "functions": [
            {
                "type": "function",
                "function": {
                    "name": "collect_marital_status",
                    "handler": collect_marital_status,
                    "description": "Record marital status",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "marital_status": {"type": "string", "enum": ["single", "married"]}
                        },
                        "required": ["marital_status"],
                    },
                    "transition_callback": handle_marital_status_collection,
                },
            }
        ],
    }


def create_quote_calculation_node(age: int, marital_status: str) -> NodeConfig:
    """Create node for calculating initial quote."""
    return {
        "task_messages": [
            {
                "role": "system",
                "content": (
                    f"Calculate a quote for {age} year old {marital_status} customer. "
                    "First, call calculate_quote with their information. "
                    "Then explain the quote details and ask if they'd like to adjust coverage."
                ),
            }
        ],
        "functions": [
            {
                "type": "function",
                "function": {
                    "name": "calculate_quote",
                    "handler": calculate_quote,
                    "description": "Calculate initial insurance quote",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "age": {"type": "integer"},
                            "marital_status": {
                                "type": "string",
                                "enum": ["single", "married"],
                            },
                        },
                        "required": ["age", "marital_status"],
                    },
                    "transition_callback": handle_quote_calculation,
                },
            }
        ],
    }


def create_quote_results_node(
    quote: Union[QuoteCalculationResult, CoverageUpdateResult],
) -> NodeConfig:
    """Create node for showing quote and adjustment options."""
    return {
        "task_messages": [
            {
                "role": "system",
                "content": (
                    f"Quote details:\n"
                    f"Monthly Premium: ${quote['monthly_premium']:.2f}\n"
                    f"Coverage Amount: ${quote['coverage_amount']:,}\n"
                    f"Deductible: ${quote['deductible']:,}\n\n"
                    "Explain these quote details to the customer. When they request changes, "
                    "use update_coverage to recalculate their quote. Explain how their "
                    "changes affected the premium and compare it to their previous quote. "
                    "Ask if they'd like to make any other adjustments or if they're ready "
                    "to end the quote process."
                ),
            }
        ],
        "functions": [
            {
                "type": "function",
                "function": {
                    "name": "update_coverage",
                    "handler": update_coverage,
                    "description": "Recalculate quote with new coverage options",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "coverage_amount": {"type": "integer"},
                            "deductible": {"type": "integer"},
                        },
                        "required": ["coverage_amount", "deductible"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "end_quote",
                    "handler": end_quote,
                    "description": "Complete the quote process",
                    "parameters": {"type": "object", "properties": {}},
                    "transition_callback": handle_end_quote,
                },
            },
        ],
    }


def create_end_node() -> NodeConfig:
    """Create the final node."""
    return {
        "task_messages": [
            {
                "role": "system",
                "content": (
                    "Thank the customer for their time and end the conversation. "
                    "Mention that a representative will contact them about the quote."
                ),
            }
        ],
        "functions": [],
        "post_actions": [{"type": "end_conversation"}],
    }


async def main():
    """Main function to set up and run the insurance quote bot."""
    async with aiohttp.ClientSession() as session:
        (room_url, _) = await configure(session)

        # Initialize services
        transport = DailyTransport(
            room_url,
            None,
            "Insurance Quote Bot",
            DailyParams(
                audio_out_enabled=True,
                vad_enabled=True,
                vad_analyzer=SileroVADAnalyzer(),
                vad_audio_passthrough=True,
            ),
        )

        stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))
        tts = DeepgramTTSService(api_key=os.getenv("DEEPGRAM_API_KEY"), voice="aura-helios-en")
        llm = OpenAILLMService(api_key=os.getenv("OPENAI_API_KEY"), model="gpt-4o")

        context = OpenAILLMContext()
        context_aggregator = llm.create_context_aggregator(context)

        # Create pipeline
        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                context_aggregator.user(),
                llm,
                tts,
                transport.output(),
                context_aggregator.assistant(),
            ]
        )

        task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))

        # Initialize flow manager with transition callback
        flow_manager = FlowManager(
            task=task, llm=llm, context_aggregator=context_aggregator, tts=tts
        )

        @transport.event_handler("on_first_participant_joined")
        async def on_first_participant_joined(transport, participant):
            await transport.capture_participant_transcription(participant["id"])
            logger.debug("Initializing flow")
            await flow_manager.initialize()
            logger.debug("Setting initial node")
            await flow_manager.set_node("initial", create_initial_node())

        # Run the pipeline
        runner = PipelineRunner()
        await runner.run(task)


if __name__ == "__main__":
    asyncio.run(main())
