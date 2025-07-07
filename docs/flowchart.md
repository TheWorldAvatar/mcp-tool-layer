```mermaid  
flowchart TD
    %% Entry Points
    A1(["High-Level Task Prompt"]):::Input --> B1["Task Decomposition Agent"]:::Agent
    A2(["Resources Folder"]):::Input --> B2["Data Sniffing Agent"]:::Agent

    %% Data Sniffing Output
    B2 --> C1["resources.json"]:::File
    B2 --> C2["data_sniffing_report.md"]:::File

    %% Task Decomposition Output
    C2 --> B1
    B1 --> D1["task_001.json"]:::Task
    B1 --> D2["task_002.json"]:::Task
    B1 --> D3["task_003.json"]:::Task

    %% Task Evaluation
    D1 --> E1["Task Evaluation Agent"]:::Agent
    D2 --> E1
    D3 --> E1

    %% Evaluation Output
    E1 --> F1["selected_task_001.json"]:::Task
    E1 --> F2["selected_task_003.json"]:::Task

    %% Task Refinement
    F1 --> G1["Task Refinement Agent"]:::Agent
    F2 --> G1
    G1 --> H1["refined_task_001.json"]:::Task
    G1 --> H2["refined_task_003.json"]:::Task

    %% Code Generation
    H1 --> I1["Code Generation Agent"]:::Agent
    H2 --> I1
    C1 --> I1

    %% Code Output
    I1 --> J1["code_task_001.py"]:::Code
    I1 --> J2["code_task_003.py"]:::Code

    %% Execution Sandbox
    J1 --> K1["Code Sandbox"]:::Runtime
    J2 --> K1

    %% Styles
    classDef Input fill:#27654A,stroke:#254336,color:#FFFFFF
    classDef Agent fill:#FFDFE5,stroke:#FF5978,color:#8E2236
    classDef Task fill:#E7EAF6,stroke:#A1A7C1,color:#2C3E50
    classDef File fill:#E9FFE6,stroke:#3C9A5F,color:#2C5F3D
    classDef Code fill:#FDF5DC,stroke:#D4A144,color:#6B4900
    classDef Runtime fill:#D3ECF9,stroke:#3399CC,color:#1C3E57
