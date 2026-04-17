import argparse
import requests

ADZUNA_BASE_URL = 'https://api.adzuna.com/v1/api/jobs/us/search/1'


def fetch_jobs(position: str, num_results: int = 5, location: str = 'united states', sort_by: str = 'relevance', app_id: str = '', app_key: str = '') -> list[dict]:
    """
    Search Adzuna for active US job postings matching the given position.
    Returns a list of job dicts with title, company, location, salary, and url.
    app_id and app_key must be provided explicitly (e.g. from user input or env vars).
    """
    if not app_id or not app_key:
        raise ValueError('Adzuna App ID and API Key are required.')

    params = {
        'app_id': app_id,
        'app_key': app_key,
        'what': position,
        'where': location,
        'results_per_page': num_results,
        'sort_by': sort_by,
        'content-type': 'application/json',
    }

    response = requests.get(ADZUNA_BASE_URL, params=params)
    response.raise_for_status()
    data = response.json()

    jobs = []
    for listing in data.get('results', []):
        salary_min = listing.get('salary_min')
        salary_max = listing.get('salary_max')
        if salary_min and salary_max:
            salary = f'${salary_min:,.0f} - ${salary_max:,.0f}'
        elif salary_min:
            salary = f'${salary_min:,.0f}+'
        elif salary_max:
            salary = f'Up to ${salary_max:,.0f}'
        else:
            salary = 'Not listed'

        jobs.append({
            'title': listing.get('title', 'N/A'),
            'company': listing.get('company', {}).get('display_name', 'N/A'),
            'location': listing.get('location', {}).get('display_name', 'N/A'),
            'salary': salary,
            'url': listing.get('redirect_url', 'N/A'),
        })

    return jobs


def print_jobs(position: str, jobs: list[dict]) -> None:
    print(f"\nTop {len(jobs)} job postings for: {position}\n")
    print("-" * 60)
    for i, job in enumerate(jobs, 1):
        print(f"{i}. {job['title']}")
        print(f"   Company:  {job['company']}")
        print(f"   Location: {job['location']}")
        print(f"   Salary:   {job['salary']}")
        print(f"   Link:     {job['url']}")
        print()


if __name__ == '__main__':
    import os
    from dotenv import load_dotenv

    load_dotenv('env_vars.env')
    _app_id = os.getenv('ADZUNA_ID', '')
    _app_key = os.getenv('ADZUNA_KEY', '')
    if not _app_id or not _app_key:
        raise SystemExit('Error: ADZUNA_ID and ADZUNA_KEY not found in env_vars.env')

    parser = argparse.ArgumentParser(description='Fetch active job postings from Adzuna.')
    parser.add_argument('position', type=str, help='Job position to search for (e.g. "data scientist")')
    parser.add_argument('--num', type=int, default=5, help='Number of results to return (default: 5)')
    parser.add_argument('--location', type=str, default='united states', help='Location to search in (default: united states)')
    parser.add_argument('--sort', type=str, default='relevance', choices=['relevance', 'date', 'salary'], help='Sort results by relevance, date, or salary (default: relevance)')
    args = parser.parse_args()

    jobs = fetch_jobs(args.position, num_results=args.num, location=args.location, sort_by=args.sort, app_id=_app_id, app_key=_app_key)
    print_jobs(args.position, jobs)
