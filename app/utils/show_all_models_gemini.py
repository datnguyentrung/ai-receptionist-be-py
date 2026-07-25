import asyncio

from google import genai

from app.core.config import settings

client = genai.Client(api_key=settings.GEMINI_API_KEY)


async def show_all_models():
    print(f"{'Model Name':<40} | {'Supported Actions'}")
    print("-" * 70)

    # BƯỚC FIX: await để lấy danh sách model trước
    models_response = await client.aio.models.list()

    # Sau đó dùng vòng lặp for bình thường để duyệt qua danh sách
    for m in models_response:
        # Lọc ra các model có khả năng tạo nội dung (generateContent)
        if "generateContent" in m.supported_actions:
            print(f"{m.name:<40} | {m.supported_actions}")


if __name__ == "__main__":
    asyncio.run(show_all_models())
