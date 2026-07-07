import asyncio
import edge_tts

async def main():
    communicate = edge_tts.Communicate("Hello world, this is a test to generate audio and check duration.", "en-US-ChristopherNeural")
    await communicate.save("test_audio.mp3")

asyncio.run(main())
