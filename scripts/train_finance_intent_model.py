"""Train and write the bundled local Finance Analyst intent model."""

from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.finance_intent_model import FinanceIntentModel, MODEL_PATH, TRAINING_EXAMPLES


def main() -> None:
    model = FinanceIntentModel(TRAINING_EXAMPLES)
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    model.save(MODEL_PATH)

    correct = 0
    total = 0
    for expected, phrases in TRAINING_EXAMPLES.items():
        for phrase in phrases:
            predicted, _ = model.predict(phrase)
            correct += predicted == expected
            total += 1
    print(f"Saved local finance intent model: {MODEL_PATH}")
    print(f"Algorithm: Multinomial Naive Bayes | Classes: {len(model.labels)} | Synthetic examples: {total}")
    print(f"Training-set accuracy: {correct}/{total} ({correct / total:.1%})")
    print("Training-set accuracy is not an independent quality estimate; expand and review examples before production use.")


if __name__ == "__main__":
    main()
