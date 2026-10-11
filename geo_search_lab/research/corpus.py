"""Thirty authored draft tasks. No customer-log or verified-source claim."""
import hashlib
import random

from geo_search_lab.research.contracts import StudyInput
from geo_search_lab.workbench.store import encoded

CATEGORIES = ("fact", "discovery", "screening", "comparison", "time_sensitive", "linked")
# A 5 x 6 roster, not randomly sampled market traffic. Rubrics need human review.
ROWS = {
    "manufacturing": [
        ("What documentation should a US buyer request to verify a CNC supplier's ISO 9001 certification?", ["Evidence used to verify certification"], ["CNC supplier", "US buyer"]),
        ("Find manufacturers that offer private-label stainless steel water bottles to US retailers, and explain how to contact them.", ["Eligible manufacturers", "Contact routes"], ["Private-label stainless steel bottles", "US retailers"]),
        ("Find packaging manufacturers serving US brands that accept an initial order of at most 500 custom printed boxes and publish their lead times.", ["Supplier candidates", "Evidence of minimum order and lead time"], ["At most 500 boxes", "Custom printing", "US service"]),
        ("Compare domestic and overseas contract manufacturing for a US startup ordering 1,000 aluminum enclosures, including landed cost and lead-time risks.", ["Landed cost comparison", "Lead-time risk comparison"], ["US startup", "1,000 aluminum enclosures", "Domestic versus overseas"]),
        ("As of October 2, 2026, what US import requirements apply to wooden furniture from Vietnam, and which official sources should a buyer check?", ["Applicable import requirements", "Authoritative verification sources"], ["United States", "Wooden furniture from Vietnam", "As of October 2, 2026"]),
        ("For a US brand launching a reusable food container, identify contract manufacturers and explain how material testing and tooling affect supplier selection.", ["Contract manufacturer discovery", "Link material testing and tooling to selection"], ["Reusable food container", "US brand"]),
    ],
    "software": [
        ("What is the difference between per-seat and usage-based pricing for customer support software?", ["Explain the pricing mechanisms", "Identify the cost drivers"], ["Customer support software"]),
        ("Find help-desk platforms suitable for a small US ecommerce team that handles both email and live chat.", ["Suitable help-desk platforms"], ["Small US ecommerce team", "Email and live chat"]),
        ("Find CRM services for a 10-person US team with published pricing below $40 per user per month and a native email integration.", ["CRM candidates", "Pricing and integration evidence"], ["10-person US team", "Below $40 per user per month", "Native email integration"]),
        ("Compare hosted and self-hosted knowledge bases for a US company, focusing on total cost, access controls, and maintenance work.", ["Total cost comparison", "Access-control comparison", "Maintenance comparison"], ["Hosted versus self-hosted", "US company"]),
        ("As of October 2, 2026, find the current free-plan limits of popular project management tools available to US businesses using official pricing pages.", ["Current free-plan limits", "Official pricing evidence"], ["As of October 2, 2026", "US business availability"]),
        ("Help a US retailer choose inventory software and explain how its Shopify integration would change order fulfillment and stock reconciliation.", ["Inventory software candidates", "Connect integration to fulfillment", "Stock reconciliation implications"], ["US retailer", "Shopify integration"]),
    ],
    "consumer": [
        ("What measurements help determine whether an office chair will fit a tall adult?", ["Relevant fit measurements"], ["Tall adult", "Office chair"]),
        ("Find independent online brands selling repairable backpacks to customers in the United States.", ["Eligible independent brands", "Evidence of repair support"], ["Backpacks", "US customers"]),
        ("Find induction-compatible cookware sold online in the US for under $200 per set, excluding nonstick coatings.", ["Eligible cookware sets", "Price and material evidence"], ["Induction-compatible", "Under $200 per set", "No nonstick coating", "US online availability"]),
        ("Compare buying a refurbished and a new espresso machine for home use in the US, focusing on warranty, total cost, and repair support.", ["Warranty comparison", "Total cost comparison", "Repair support comparison"], ["Refurbished versus new", "US home use"]),
        ("As of October 2, 2026, what return policies do online mattress brands currently offer US customers, including pickup fees and trial periods?", ["Current trial periods", "Return and pickup charges"], ["US customers", "As of October 2, 2026", "Online mattress brands"]),
        ("Find water filters for a US household using well water and explain how a water-test result should guide product selection and replacement costs.", ["Candidate filtration approaches", "Link test results to selection", "Replacement cost drivers"], ["US household", "Well water"]),
    ],
    "logistics": [
        ("What is dimensional weight and how does it affect parcel shipping charges for US ecommerce sellers?", ["Dimensional-weight calculation", "Effect on shipping charges"], ["US ecommerce parcels"]),
        ("Find third-party fulfillment providers serving US independent online stores that ship fragile home decor.", ["Suitable fulfillment providers", "Evidence of fragile-goods handling"], ["US independent online stores", "Fragile home decor"]),
        ("Find US fulfillment providers that accept fewer than 300 orders per month and can process customer returns without an annual contract.", ["Eligible fulfillment providers", "Evidence of eligibility and returns service"], ["Fewer than 300 orders per month", "No annual contract", "US fulfillment"]),
        ("Compare in-house fulfillment and a third-party warehouse for a US store shipping 500 orders per month, including storage, labor, and returns costs.", ["Storage cost comparison", "Labor cost comparison", "Returns cost comparison"], ["US store", "500 orders per month", "In-house versus third party"]),
        ("As of October 2, 2026, what published holiday shipping deadlines should a US online store use for domestic delivery before December 25?", ["Published holiday deadlines", "Carrier-specific service conditions"], ["As of October 2, 2026", "US domestic delivery", "Before December 25, 2026"]),
        ("For a US skincare store, identify fulfillment partners and explain how storage temperature and returns rules should shape the outsourcing decision.", ["Fulfillment partner discovery", "Connect temperature requirements to selection", "Connect returns rules to selection"], ["US skincare store"]),
    ],
    "services": [
        ("What deliverables should be included in an accessibility audit of a US ecommerce website?", ["Audit deliverables", "Evidence and remediation guidance"], ["US ecommerce website"]),
        ("Find bookkeeping firms that work remotely with small US ecommerce businesses and describe their service scope.", ["Eligible bookkeeping firms", "Service scope"], ["Remote service", "Small US ecommerce businesses"]),
        ("Find product photography studios in the US that publish pricing for batches of 20 products and accept products by mail.", ["Eligible photography studios", "Batch pricing evidence"], ["20 products", "Mail-in service", "US studios"]),
        ("Compare hiring a freelance designer and a design agency for a US manufacturer's website redesign, including cost structure, ownership, and ongoing support.", ["Cost structure comparison", "Ownership terms", "Support comparison"], ["US manufacturer website", "Freelancer versus agency"]),
        ("As of October 2, 2026, find the current service packages and cancellation terms of remote bookkeeping providers for US small businesses.", ["Current service packages", "Cancellation terms"], ["Remote bookkeeping", "US small businesses", "As of October 2, 2026"]),
        ("For a US manufacturer entering direct-to-consumer sales, find ecommerce implementation agencies and explain how platform choice affects migration and maintenance costs.", ["Agency candidates", "Link platform choice to migration", "Maintenance costs"], ["US manufacturer", "Direct-to-consumer launch"]),
    ],
}


def draft_corpus():
    cases = []
    for sector, rows in ROWS.items():
        for category, (prompt, needs, constraints) in zip(CATEGORIES, rows):
            cases.append({"family_id": f"{sector}-{category}", "sector": sector, "category": category,
                          "prompt": prompt, "rubric": [
                              {"item_id": f"{prefix}{i}", "kind": kind, "text": text}
                              for kind, prefix, values in (("need", "N", needs), ("constraint", "C", constraints))
                              for i, text in enumerate(values, 1)],
                          "provenance": "Authored simulation; not a customer log. Public-source grounding and independent family review pending.",
                          "source_urls": [], "source_status": "pending_verification", "captured_at": None,
                          "reference_at": "2026-10-02T00:00:00Z", "review_status": "draft"})
    return cases


def manifest(value: StudyInput):
    cases = sorted([c.model_dump() for c in value.cases], key=lambda c: c["family_id"])
    if len({c["family_id"] for c in cases}) != 30 or len({c["prompt"] for c in cases}) != 30:
        raise ValueError("必须有 30 个唯一需求家族与问题")
    if len({(c["sector"], c["category"]) for c in cases}) != 30:
        raise ValueError("需要完整的五行业 × 六需求类别矩阵")
    rng = random.Random(value.seed)
    partitions, diagnostics = {}, set()
    for cat in CATEGORIES:
        families = [c["family_id"] for c in cases if c["category"] == cat]
        rng.shuffle(families)
        partitions.update({f: "development" if i < 3 else "audit" for i, f in enumerate(families)})
        diagnostics.add(rng.choice(families[:3]))
    slots, schedule = [], []
    for case in cases:
        family = case["family_id"]
        selected = rng.randint(1, 5)
        for repeat in range(1, 6):
            block = []
            for arm, temperature in (("A", 0.4), ("B", 0.0)):
                slot_id = "s_" + hashlib.sha256(f"{value.study_id}:{value.version}:{value.seed}:{family}:{repeat}:{arm}".encode()).hexdigest()[:24]
                slots.append({"slot_id": slot_id, "family_id": family, "arm": arm, "temperature": temperature,
                              "repeat": repeat, "partition": partitions[family], "reference": repeat == selected,
                              "diagnostic": family in diagnostics and repeat == selected,
                              "generation_seed": 100000 + value.seed + repeat,
                              "case": case})
                block.append(slot_id)
            rng.shuffle(block)
            schedule.append(block)
    rng.shuffle(schedule)
    rng.shuffle(slots)
    # Store exact order so reproduction does not depend on future RNG implementations.
    result = {"schema_version": "research-1", "study_id": value.study_id, "version": value.version,
              "seed": value.seed, "data_kind": value.data_kind, "slots": slots,
              "execution_order": [s for block in schedule for s in block],
              "algorithm": "stratified-family-v1", "source": value.model_dump()}
    result["manifest_sha256"] = hashlib.sha256(encoded(result).encode()).hexdigest()
    return result
