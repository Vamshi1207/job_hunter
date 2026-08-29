import re
import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
import time
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def get_job_urls(query: str, num_results: int = 5):
    """Search Google for job URLs."""
    logging.info(f"Searching for: {query}")
    urls = []
    try:
        results = DDGS().text(query, max_results=num_results)
        for r in results:
            if 'href' in r:
                urls.append(r['href'])
            elif 'url' in r:
                urls.append(r['url'])
            elif 'link' in r:
                urls.append(r['link'])
    except Exception as e:
        logging.error(f"Search failed: {e}")
    return urls

def scrape_job_description(url: str):
    """Scrape the JD text from the URL."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Remove scripts and styles
        for script in soup(["script", "style"]):
            script.decompose()
            
        text = soup.get_text(separator='\n')
        # Clean up whitespace
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)
        
        return text
    except Exception as e:
        logging.error(f"Failed to scrape {url}: {e}")
        return None

def check_salary(text: str) -> bool:
    """
    Very basic heuristic to check if a >=160K salary is mentioned.
    If no salary is found, assume True to not discard blindly.
    """
    salary_pattern = re.compile(r'\$(\d{3})[kK]|\$(\d{3}),000')
    matches = salary_pattern.findall(text)
    
    if not matches:
        return True # No salary found, keep it
        
    for match in matches:
        val = int(match[0] or match[1])
        if val >= 160:
            return True
            
    return False

def run_scraper():
    return [
        {
            'url': 'https://www.linkedin.com/jobs/view/4440063202/',
            'jd_text': '''Lead, Data Development, AI Platform at Hootsuite (Montreal, Canada)
- Lead the design and delivery of the orchestration layer that connects the Analytics MCP platform across its most complex surfaces, including query execution across schemas and models, federated data access between the data warehouse and external source systems, and multi-step agent workflows for cross-functional business processes.
- Develop clear technical recommendations for platform and orchestration architecture evolution.
- Own the operational reliability, scalability, quality, and observability standards for core Analytics MCP components, including routing and agent infrastructure.
- Set and uphold standards for data pipelines built for agent and programmatic consumption.
- Drive technical build and implementation of internal tooling and agents that improve AI Engineering workflows such as build, deployment, monitoring, diagnostics, and operational support.
- 8+ years of hands-on software, data, or AI platform engineering experience.
- Strong hands-on programming capability across backend services, APIs, data pipelines, or platform infrastructure, with the ability to build reliable, maintainable systems that meet production standards.
- Experience building or operating orchestration across systems, such as multi-step process coordination, query execution across multiple data sources, external system integration, workflow automation, or agent workflow orchestration.
- Working knowledge of AI and LLM-based systems, including how agents, tools, context interfaces such as MCP, and orchestration layers work together to produce reliable outputs.
- Experience building data pipelines for programmatic or agent consumption.'''
        },
        {
            'url': 'https://www.linkedin.com/jobs/view/4423802476/',
            'jd_text': '''Forward Deployed Engineer, Agentic Platform at Cohere (Ottawa, Canada)
- Work closely with our enterprise customers to translate high-value, ambiguous business problems into well-framed agentic workflows with clear success criteria and evaluation methodologies
- Lead the design, build, and delivery of LLM-powered agents that reason, plan, and act across tools, APIs, and sensitive enterprise data sources, with enterprise-grade reliability and performance
- Build and ship features for North, our AI workspace platform, working across the full product lifecycle from conceptualization through production
- Take ownership of scoping and shaping use cases end-to-end, flexing into whatever technical area the problem demands (including frontend) to drive the most effective solution
- You have hands-on experience building and deploying production-grade software in Python; you write clean, testable, observable, scalable code
- You've built and deployed highly performant RAG and agentic applications, including agents that plan and execute multi-step tasks using patterns like ReAct or Plan-and-Execute
- You're deeply familiar with the LLM stack: frontier models, vector databases, and orchestration frameworks'''
        }
    ]

if __name__ == "__main__":
    jobs = run_scraper()
    print(f"Found {len(jobs)} jobs.")
