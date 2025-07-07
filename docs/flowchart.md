---
config:
  theme: redux
  layout: dagre
---
flowchart TD
    A2(["resources folder"]) --> B["Data 
    Sniffing 
    Agent"] & n1(["Code sandbox"])
    A(["High level prompt:
        defining the overall task"]) --> B & E["Task 
    Decomposition 
    Agent"] & I["Code 
    Generartion 
    Agent"] & H["Task 
    Refinement 
    Agent"]
    B --> C["resources.json"] & D["data_sniffing_report.md"]
    D --> E
    E --> F1["task_001.json"] & F2["task_002.json"] & F3["task_003.json"]
    F1 --> G["Task 
     Evalaution 
     Agent"]
    F2 --> G
    F3 --> G
    G --> SF1["task_001.json"] & SF2["task_003.json"]
    SF1 --> H
    SF2 --> H
    H --> RF1["refined_task_001.json"] & RF2["refined_task_003.json"]
    RF1 --> I
    RF2 --> I
    I --> n2["extra code for task_001"] & n3["extra code for task_003"]
    n2 --> n1
    n3 --> n1
    C --> I
    B@{ shape: tri}
    E@{ shape: tri}
    I@{ shape: tri}
    H@{ shape: tri}
    G@{ shape: tri}
     B:::Rose
     A:::Pine
     E:::Rose
     I:::Rose
     H:::Rose
     G:::Rose
    classDef Rose stroke-width:1px, stroke-dasharray:none, stroke:#FF5978, fill:#FFDFE5, color:#8E2236
    classDef Pine stroke-width:1px, stroke-dasharray:none, stroke:#254336, fill:#27654A, color:#FFFFFF
