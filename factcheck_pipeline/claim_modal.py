import openai

client = openai.OpenAI(api_key="API_KEY")

def detect_media_type_in_claim(claim_text):
    response = client.chat.completions.create(
        model="gpt-4o",
        temperature=0.0,
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a professional fact-checking journalist. "
                    "Your task is to classify the type of media or reference used in a given claim. "
                    "Each claim may refer to a photo, a video, a statistic (such as a chart, graph, or number), or just plain text without any visual or numerical reference.\n\n"
                    "Only respond with one of the following labels:\n"
                    "- photo: if the claim references or is based on an image or photograph\n"
                    "- video: if the claim references or is based on a video clip\n"
                    "- statistic: if the claim is based on a chart, graph, percentage, or numeric data\n"
                    "- text: if the claim is not based on any visual or numerical content\n"
                )
            },
            {
                "role": "user",
                "content": (
                    "Classify the claim by type.\n"
                    "Respond with:\nClaim Type: photo / video / statistic / text\n\n"
                    "### Example 1:\n"
                    "Claim: A video shows tanks rolling into Washington D.C. during the protests.\n"
                    "Claim Type: video\n\n"
                    "### Example 2:\n"
                    "Claim: This photo proves that the Prime Minister was not present at the UN summit.\n"
                    "Claim Type: photo\n\n"
                    "### Example 3:\n"
                    "Claim: A Statista graph shows the number of mercenaries in Ukraine.\n"
                    "Claim Type: statistic\n\n"
                    "### Example 4:\n"
                    "Claim: It's impossible that temperatures reached 60 degrees Celsius in Canada.\n"
                    "Claim Type: text\n\n"
                    f"### Now classify this claim:\nClaim: {claim_text}"
                )
            }
        ]
    )

    full_reply = response.choices[0].message.content
    for line in full_reply.splitlines():
        if line.lower().startswith("claim type:"):
            return line.split(":", 1)[1].strip().lower()

    return "unknown"

result = detect_media_type_in_claim(claim_text)
