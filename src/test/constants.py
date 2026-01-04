import os

# Imposta qui il percorso assoluto della cartella che contiene i tuoi progetti Java da analizzare
# Esempio Windows: "C:\\Users\\Nome\\Documents\\JavaProjects"
# Esempio Mac/Linux: "/home/user/java_projects"

DIRECTORY_REPOS = "C:\\Users\\lampa\\Documents\\GitHub"

# Header standard se servono per chiamate HTTP future
HEADER = {
    "Content-Type": "application/x-www-form-urlencoded"
}

# La metrica non serve più come costante fissa perché usiamo la ricerca issues generica, 
# ma la lasciamo per compatibilità se servisse
METRIC_TO_REFACTOR = "energy_smells"

JAVA_COLLECTION_RULES = """
STRICT REFACTORING RULES FOR "Avoid Java Collection Framework" ISSUES:

You MUST strictly adhere to the following mapping when replacing collections. 
Do NOT use types not explicitly listed below (e.g., do not use 'MutableList' as a type if 'List' suffices, and use the concrete classes listed for instantiation).

MAPPING RULES:

1. **Original**: `ArrayList`
   - **Alternatives**: 
     - `FastList` (Eclipse Collections)
     - `TreeList` (Apache Commons)
     - `NodeCachingLinkedList` (Apache Commons)

2. **Original**: `LinkedList`
   - **Alternatives**: 
     - `TreeList` (Apache Commons)
     - `FastList` (Eclipse Collections)
     - `ArrayList` (JCF) *[Only if random access is predominant]*

3. **Original**: `Vector`
   - **Alternatives**: 
     - `SynchronisedFastList` (Eclipse Collections)
     - `SynchronisedArrayList` (JCF)

4. **Original**: `HashMap`
   - **Alternatives**: 
     - `HashedMap` (Apache Commons Collections)
     - `UnifiedMap` (Eclipse Collections)

5. **Original**: `Hashtable`
   - **Alternatives**: 
     - `ConcurrentHashMap` (JCF)
     - `ConcurrentHashMapEC` (Eclipse Collections)
     - `StaticBucketMap` (Apache Commons Collections)

6. **Original**: `ConcurrentHashMap`
   - **Alternatives**: 
     - `ConcurrentHashMapEC` (Eclipse Collections)

DEPENDENCY & PACKAGE INFO:
- **Eclipse Collections**: `org.eclipse.collections` (e.g., `org.eclipse.collections.impl.list.mutable.FastList`, `org.eclipse.collections.impl.map.mutable.UnifiedMap`)
- **Apache Commons Collections**: `org.apache.commons.collections` (e.g., `org.apache.commons.collections4.list.TreeList`, `org.apache.commons.collections4.map.HashedMap`)
- **JCF**: `java.util.*` (Java Collection Framework)
"""