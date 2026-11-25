"""Run the actual detector via its MCP backend and save outputs separately from labels."""
import argparse,asyncio,json,sys,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import analyze

async def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("input",type=Path);ap.add_argument("output",type=Path)
    args=ap.parse_args()
    if os.getenv("EDITOR_RUBRIC_PATH") and not Path(os.environ["EDITOR_RUBRIC_PATH"]).is_file():
        raise FileNotFoundError("Проверьте EDITOR_RUBRIC_PATH")
    examples=json.loads(args.input.read_text())
    with args.output.open("x",encoding="utf8") as f:
        for key,item in examples.items():
            text=item if isinstance(item,str) else item["text"]
            context="" if isinstance(item,str) else item.get("brief","")
            j=await analyze("sample.txt",text.encode(),context=context)
            f.write(json.dumps(dict(id=key,backend=j["backend"],seen=j["seen"],total=j["total"],
                findings=j["findings"],summary=j["summary"],model=j["model"],elapsed=j["elapsed"],usage=j["usage"],variant=os.getenv("EDITOR_DETECTOR_VARIANT","product")),ensure_ascii=False)+"\n")
            f.flush()
if __name__=="__main__":asyncio.run(main())
