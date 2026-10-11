"""Read-only authored prompt presets; selecting a case never starts an experiment."""
from geo_search_lab.pilot_common import ROOT, read_json
from geo_search_lab.workbench.contracts import RunRequest

LIBRARY = ROOT / 'data/benchmarks/prospect_prompt_suite_v1.json'


def load_prompt_library():
    source = read_json(LIBRARY)
    companies = []
    for company in source['companies']:
        cases = []
        for case in company['cases']:
            # Company and brand-awareness grouping uses the existing public contract.
            request = RunRequest(
                prompt=case['prompt'],
                title=f"{case['id']} {company['name']} · {case['title']}",
                experiment_id=f"prospect-{company['id'].lower()}-{case['awareness']}-v1",
                input_mode='empty_ablation',
                facets=case['facets'], constraints=case['constraints'],
            )
            cases.append({
                'id': case['id'], 'title': case['title'], 'kind': case['kind'],
                'awareness': case['awareness'], 'variant_of': case['variant_of'],
                'request': request.model_dump(exclude_none=True),
            })
        companies.append({'id': company['id'], 'name': company['name'], 'cases': cases})
    return {'schema_version': source['schema_version'], 'suite_id': source['suite_id'],
            'status': source['status'], 'description': source['description'],
            'companies': companies}
