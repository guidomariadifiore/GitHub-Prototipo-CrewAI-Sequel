import os

# Imposta qui il percorso assoluto della cartella che contiene i tuoi progetti Java da analizzare
# Esempio Windows: "C:\\Users\\Nome\\Documents\\JavaProjects"
# Esempio Mac/Linux: "/home/user/java_projects"

DIRECTORY_REPOS = "G:\\Thesis stuff\\Projects\\Apache" # CHANGE HERE TO CHANGE PROJECTS DIRECTORY

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

MAVEN_DEPENDENCIES = {
    "eclipse_collections": """
        <!-- Eclipse Collections -->
        <dependency>
            <groupId>org.eclipse.collections</groupId>
            <artifactId>eclipse-collections-api</artifactId>
            <version>11.1.0</version>
        </dependency>
        <dependency>
            <groupId>org.eclipse.collections</groupId>
            <artifactId>eclipse-collections</artifactId>
            <version>11.1.0</version>
        </dependency>""",
    "commons_collections": """
        <!-- Apache Commons Collections -->
        <dependency>
            <groupId>org.apache.commons</groupId>
            <artifactId>commons-collections4</artifactId>
            <version>4.4</version>
        </dependency>"""
}

CUSTOM_RULES = """
SPECIFIC REFACTORING RULES:

1. **Rule**: `creedengo-java:GCI28` ("Optimize read file exceptions")
   - **CRITICAL INSTRUCTION**: You are STRICTLY FORBIDDEN from using `try-catch` blocks for this rule.
   - **Requirement**: Use a conditional check (if-statement) to validate state.
   - **Correct Pattern**:
     ```java
     if (condition_fails) { 
         throw new SpecificException("brief description"); 
     }
     ```
   - **Incorrect Pattern (NEVER USE)**:
     ```java
     try { ... } catch (Exception e) { throw new SpecificException(...); }
     ```
     
2. **Rule**: `creedengo-java:GCI67` ("Use ++i instead of i++")
   - **IDENTIFYING THE ISSUE TIP**: The fix for this issue is pretty self-explanatory from its name. However, note that "i" is a generic variable. It could also be "sum++", "x++", etc.
"""