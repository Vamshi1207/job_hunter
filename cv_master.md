**Vamshi Shalapaati**  
**Software Engineer \- Distributed Systems, Real-Time Data Infrastructure & AI**  
Canada | 819-919-6294 | shalapaativamshi@gmail.com | [linkedin.com/in/vamshi-shalapaati](http://linkedin.com/in/vamshi-shalapaati)  
		

Software Engineer with 6+ years building production scalable distributed data and AI systems, backend infrastructure, and AI-assisted developer tooling. Experienced architecting event-driven platforms using Kafka, PySpark, AWS, and microservices while leading adoption of LLM-driven engineering workflows using Claude, ChatGPT, and internal automation tooling. Consistent track record of measurable outcomes of 25 to 46% performance gains across fraud detection, pipeline throughput, and workflow automation delivered across high-volume production systems at scale.

**WORK EXPERIENCE**	

**Jeppesen Foreflight – Montreal, Canada**  
*Software Engineer* 	*September 2023 – Present*

* Engineered and scaled real-time constraint evaluation models in Python for airline operations supporting 10,000+ users under continuous high-frequency operational event streams.  
* Implemented event-driven microservices using REST APIs and IBM MQ to propagate operational state changes across distributed airline platforms, reducing crew planner and tracker workload effort by 60%+.  
* Collaborated with airline stakeholders to translate operational requirements into technical specifications, reducing change request turnaround time by 30%.  
* Led adoption of LLM-assisted engineering workflows across the team, integrating Claude and ChatGPT with Jira, GitLab, and internal systems via MCP, reducing context-switching and improving developer productivity.  
* Built reusable domain-specific AI tooling and agentic engineering workflows to improve debugging, code navigation, dependency tracing, and large-scale codebase analysis across enterprise repositories.  
* Developed observability workflows and Grafana dashboards for distributed system performance, CI/CD reliability, and operational bottleneck detection, with AI-assisted anomaly analysis to accelerate incident response.  
* Established automated testing standards using Pytest and Gherkin, increasing regression coverage by 40%, and defined code review processes mentoring junior engineers and preventing critical production defects.

**Randstad – Montreal, Canada**  
*Data Engineer*	*April 2022 – September 2023*

* Designed and deployed an NLP text classification pipeline in Python (Scikit-learn) to categorise unstructured customer feedback at scale, surfacing product and support issues faster than manual review.  
* Built scalable data ingestion pipelines and automation scripts using Python and REST APIs to collect and normalise operational data from multiple internal systems, powering downstream analytics workflows.  
* Developed Power BI dashboards integrating API-sourced datasets with data transformation and aggregation, enabling support teams to identify and act on recurring issues.  
* Automated shift scheduling and workforce planning via scripting, saving 2 days of manual effort per month.

**Uber Technologies – Hyderabad, India**  
*Software Engineer*	*January 2019 – August 2021*

* Engineered low-latency fraud detection services in Python for high-volume transaction streams, reducing fraudulent activity by 25% through caching, memoisation, and redundant computation elimination.  
* Architected a high-throughput real-time event processing pipeline on Apache Kafka for distributed fraud auditing, selecting event-driven stream processing over batch architectures to meet sub-second detection SLAs. Applied parallelisation and compression strategies that improved throughput by 46% and reduced false positives by 38%.  
* Designed and deployed supervised ML inference pipelines for real-time fraud classification, improving detection accuracy by 20% through feature engineering, model optimisation, and production traffic validation.  
* Optimised distributed PySpark data processing workloads and large-scale SQL execution plans across analytical pipelines, reducing end-to-end processing latency by 28% for high-volume fraud analytics systems.  
* Designed operational intelligence dashboards and reporting pipelines on top of distributed fraud processing systems, improving visibility into live transaction anomalies, pipeline health, and fraud detection performance.  
* Built resilient Python and Shell-based event routing pipelines using asynchronous processing patterns, queue-based orchestration, and failure handling mechanisms, reducing fraud incident response time by 32%.

**KEY SKILLS**

**Programming languages:** Python, JavaScript, SQL, Shell scripting, C, YAML  
**ML & AI:** LLM-assisted engineering workflows, AI developer tooling, MCP integrations, NLP pipelines, text classification, supervised/unsupervised ML  
**Distributed systems & data:** Apache Kafka, PySpark, distributed data processing, event-driven architecture, real-time streaming pipelines, ETL, time-series analytics, performance optimisation  
**Backend & APIs:** REST APIs, microservices, Flask, WebSockets, scalable system design, SQL query optimisation  
**Cloud & DevOps:** AWS (EC2, S3, Lambda, SQS, CloudWatch), Docker, Kubernetes, Jenkins, CI/CD pipelines, observability & monitoring

**EDUCATION**	

**Bishop’s University – Sherbrooke, Canada**	*September 2021 – January 2023*  
Master of Science in Computer Science  
**Focus: Machine Learning, Distributed systems, data engineering, and scalable computing**

**Jawaharlal Nehru Technological University – Hyderabad, India**	*October 2013 – June 2017*  
Bachelor of Technology in Computer Science and Engineering

**PROJECTS**	

**Containerised Media Streaming Platform**   
**Tech: Node.js, Docker, Docker Compose, REST APIs, Cloudflare Tunnels, GHCR | [GitHub](https://github.com/Vamshi1207/Media_Streaming_Lab)** 

* Designed and built a fully containerised media streaming platform orchestrating 9 Docker services including a custom Node.js aggregation backend, Jellyfin streaming server, automated media acquisition pipeline (Radarr, Sonarr, Prowlarr), and a request management layer deployed via a single Docker Compose configuration.  
* Architected a static IP subnet (172.20.0.0/16) with deterministic per-service addressing, selecting fixed IPs over DNS-based service discovery to eliminate lookup overhead and ensure predictable inter-service routing at runtime.  
* Built and published 8 custom Docker images to GitHub Container Registry (GHCR), establishing versioned, reproducible deployments and a self-owned image registry for the entire platform.  
* Integrated Cloudflare Tunnel as an optional Docker Compose profile to enable zero-port-exposure remote access over HTTP/2, decoupling remote connectivity from core platform operation and eliminating the need for inbound firewall rules.  
* Built a custom OpenSubtitles web scraper sidecar service to replace the paid API dependency, demonstrating deliberate cost vs complexity tradeoff analysis in system design decisions.


**Real-Time Streaming Trading Analytics Platform**  
**Tech: Python, Flask, WebSockets, REST API, Pandas, NumPy | [GitHub](https://github.com/Vamshi1207/Crypto-analysis)**

* Designed and built a real-time analytics platform with WebSocket-based data ingestion, a Python Flask backend, and a scalable streaming pipeline processing high-frequency event streams with sub-second throughput.  
* Architected a time-series processing engine to ingest, normalise, and deduplicate multi-interval datasets, implementing in-memory retention and windowed aggregation for high-volume data.  
* Built a REST API layer and feature engineering pipeline to expose computed signals and persist structured datasets for downstream ML model training and analytical workflows.  
* Designed rule-based signal detection logic with pattern recognition and proximity heuristics, producing explainable, auditable outputs.

