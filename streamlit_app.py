import streamlit as st
import pandas as pd
import re
import json
from datetime import datetime
import uuid
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
# Anthropic for LLM
import anthropic


API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

PROPERTIES_FILE = DATA_DIR / "properties.csv"
PROPOSITIONS_FILE = DATA_DIR / "propositions.csv"
LTL_FILE = DATA_DIR / "ltl_formulas.csv"
COMPLETE_FILE = DATA_DIR / "complete_formalization.csv"

class SmartRFCParser:
    """Intelligently parse RFC and extract property-rich content"""
    
    RFC_KEYWORDS = ['MUST', 'MUST NOT', 'REQUIRED', 'SHALL', 'SHALL NOT',
                    'SHOULD', 'SHOULD NOT', 'RECOMMENDED', 'MAY', 'OPTIONAL']
    
    def parse(self, text):
        """Parse RFC and extract metadata + property-rich sections"""
        
        # Extract RFC number
        rfc_match = re.search(r'RFC\s*(\d+)', text, re.IGNORECASE)
        rfc_number = rfc_match.group(1) if rfc_match else "Unknown"
        
        # Extract title (usually in first 20 lines)
        lines = text.split('\n')
        title = self._extract_title(lines[:20])
        
        # Find all sections with RFC keywords
        sections = self._extract_property_sections(text)
        
        return {
            'rfc_number': rfc_number,
            'title': title,
            'total_chars': len(text),
            'property_sections': sections
        }
    
    def _extract_title(self, lines):
        for line in lines:
            stripped = line.strip()
            if len(stripped) > 15 and not stripped.startswith('RFC'):
                return stripped[:100]
        return "Unknown Title"
    
    def _extract_property_sections(self, text):
        """Extract sections that contain properties"""
        
        sections = []
        
        # Split by section numbers
        section_pattern = r'^(\d+(?:\.\d+)*\.?)\s+(.+?)$'
        lines = text.split('\n')
        
        current_section = None
        current_title = ""
        current_content = []
        
        for line in lines:
            match = re.match(section_pattern, line.strip())
            
            if match:
                # Save previous section if it has keywords
                if current_section and current_content:
                    content = '\n'.join(current_content)
                    keyword_count = self._count_keywords(content)
                    
                    if keyword_count >= 3:  # At least 3 keywords = likely has properties
                        sections.append({
                            'section': current_section,
                            'title': current_title,
                            'content': content,
                            'keywords': keyword_count
                        })
                
                # Start new section
                current_section = match.group(1).rstrip('.')
                current_title = match.group(2).strip()
                current_content = []
            
            elif current_section:
                current_content.append(line)
        
        # Save last section
        if current_section and current_content:
            content = '\n'.join(current_content)
            keyword_count = self._count_keywords(content)
            if keyword_count >= 3:
                sections.append({
                    'section': current_section,
                    'title': current_title,
                    'content': content,
                    'keywords': keyword_count
                })
        
        # Sort by keyword density
        sections.sort(key=lambda x: x['keywords'], reverse=True)
        
        return sections
    
    def _count_keywords(self, text):
        count = 0
        upper = text.upper()
        for kw in self.RFC_KEYWORDS:
            count += upper.count(kw)
        return count

class PropertyProcessor:
    """Process properties using Claude efficiently"""
    
    def __init__(self, api_key):
        self.client = anthropic.Anthropic(api_key=api_key)
    
    def extract_properties_batch(self, sections, rfc_number):
        """Extract properties from ALL sections in ONE call"""
        
        # Prepare combined prompt
        sections_text = ""
        for i, sec in enumerate(sections, 1):
            sections_text += f"\n\n=== SECTION {sec['section']}: {sec['title']} ===\n"
            sections_text += sec['content'][:2000]  # Limit each section
        
        prompt = f"""Analyze RFC {rfc_number} and extract ALL protocol properties.

A property is a requirement/constraint with keywords like MUST, SHOULD, MAY, etc.

For each property found, provide:
- section: Section number where found
- text: Complete property statement
- type: One of [Safety, Liveness, Ordering, Timing, Unknown]

Here are the sections:
{sections_text}

Return JSON array ONLY:
[
  {{"section": "4.2", "text": "Client MUST NOT send...", "type": "Safety"}},
  ...
]
"""
        
        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=8000,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}]
            )
            
            text = response.content[0].text
            
            # Extract JSON
            json_match = re.search(r'\[.*\]', text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                
                # Add metadata
                properties = []
                for item in data:
                    properties.append({
                        'id': str(uuid.uuid4())[:8],
                        'rfc': rfc_number,
                        'section': item.get('section', ''),
                        'text': item.get('text', ''),
                        'type': item.get('type', 'Unknown'),
                        'timestamp': datetime.now().isoformat()
                    })
                
                return properties
            
            return []
        
        except Exception as e:
            st.error(f"API Error: {e}")
            return []
    
    def extract_propositions_batch(self, properties):
        """Extract atomic propositions for multiple properties in ONE call"""
        
        # Prepare batch prompt
        properties_text = ""
        for i, prop in enumerate(properties, 1):
            properties_text += f"\n\n[PROPERTY {i}]\n"
            properties_text += f"ID: {prop['id']}\n"
            properties_text += f"Text: {prop['text']}\n"
        
        prompt = f"""Extract atomic propositions from these properties.

An atomic proposition is a basic boolean statement (action, state, event, condition).

For each property, list its propositions with:
- property_id: The property ID
- name: snake_case name
- type: One of [action, state, event, condition]
- description: What it represents

Properties:
{properties_text}

Return JSON array ONLY:
[
  {{"property_id": "abc123", "name": "client_sends_data", "type": "action", "description": "Client sends data packet"}},
  ...
]
"""
        
        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=8000,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}]
            )
            
            text = response.content[0].text
            
            json_match = re.search(r'\[.*\]', text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                
                # Add metadata
                propositions = []
                for item in data:
                    propositions.append({
                        'id': str(uuid.uuid4())[:8],
                        'property_id': item.get('property_id', ''),
                        'name': item.get('name', ''),
                        'type': item.get('type', ''),
                        'description': item.get('description', ''),
                        'timestamp': datetime.now().isoformat(),
                        'approved': False
                    })
                
                return propositions
            
            return []
        
        except Exception as e:
            st.error(f"API Error: {e}")
            return []
    
    def generate_ltl_batch(self, properties_with_propositions):
        """Generate LTL formulas for ALL properties in ONE call"""
        
        # Prepare batch prompt
        properties_text = ""
        for i, item in enumerate(properties_with_propositions, 1):
            prop = item['property']
            propositions = item['propositions']
            
            properties_text += f"\n\n[PROPERTY {i}]\n"
            properties_text += f"ID: {prop['id']}\n"
            properties_text += f"Natural Language: {prop['text']}\n"
            properties_text += f"Type: {prop['type']}\n"
            properties_text += f"Atomic Propositions:\n"
            for p in propositions:
                properties_text += f"  - {p['name']}: {p['description']}\n"
        
        prompt = f"""Generate LTL (Linear Temporal Logic) formulas from these properties using their atomic propositions.

LTL Operators:
- G (Globally/Always): Something is always true
- F (Finally/Eventually): Something eventually becomes true
- X (Next): Something is true in the next state
- U (Until): Something holds until another thing becomes true
- -> (Implies): If...then
- & (And), | (Or), ! (Not)

Common patterns:
- Safety "MUST NOT": G !(bad_thing)
- Safety "MUST...before": G (action_a -> precondition)
- Liveness "MUST eventually": G (request -> F response)
- Ordering "before": G (action_a -> X action_b)

For each property, provide:
- property_id: The property ID
- ltl_formula: The LTL formula using the atomic propositions
- explanation: Brief explanation of the formula
- operators_used: List of LTL operators used

Properties:
{properties_text}

Return JSON array ONLY:
[
  {{
    "property_id": "abc123",
    "ltl_formula": "G (client_sends_data -> handshake_complete)",
    "explanation": "Globally: if client sends data, handshake must be complete",
    "operators_used": ["G", "->"]
  }},
  ...
]
"""
        
        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=8000,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}]
            )
            
            text = response.content[0].text
            
            json_match = re.search(r'\[.*\]', text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                
                # Add metadata
                ltl_formulas = []
                for item in data:
                    ltl_formulas.append({
                        'id': str(uuid.uuid4())[:8],
                        'property_id': item.get('property_id', ''),
                        'ltl_formula': item.get('ltl_formula', ''),
                        'explanation': item.get('explanation', ''),
                        'operators_used': ','.join(item.get('operators_used', [])),
                        'timestamp': datetime.now().isoformat(),
                        'approved': False
                    })
                
                return ltl_formulas
            
            return []
        
        except Exception as e:
            st.error(f"API Error: {e}")
            return []

class DataManager:
    """Manage CSV data efficiently"""
    
    @staticmethod
    def load_properties():
        if PROPERTIES_FILE.exists():
            return pd.read_csv(PROPERTIES_FILE)
        return pd.DataFrame(columns=['id', 'rfc', 'section', 'text', 'type', 'timestamp'])
    
    @staticmethod
    def save_properties(props):
        df = pd.DataFrame(props)
        
        # Merge with existing
        if PROPERTIES_FILE.exists():
            existing = pd.read_csv(PROPERTIES_FILE)
            df = pd.concat([existing, df], ignore_index=True)
            df = df.drop_duplicates(subset=['id'], keep='last')
        
        df.to_csv(PROPERTIES_FILE, index=False)
    
    @staticmethod
    def load_propositions():
        if PROPOSITIONS_FILE.exists():
            return pd.read_csv(PROPOSITIONS_FILE)
        return pd.DataFrame(columns=['id', 'property_id', 'name', 'type', 'description', 'timestamp', 'approved'])
    
    @staticmethod
    def save_propositions(props):
        df = pd.DataFrame(props)
        
        if PROPOSITIONS_FILE.exists():
            existing = pd.read_csv(PROPOSITIONS_FILE)
            df = pd.concat([existing, df], ignore_index=True)
            df = df.drop_duplicates(subset=['id'], keep='last')
        
        df.to_csv(PROPOSITIONS_FILE, index=False)
    
    @staticmethod
    def load_ltl_formulas():
        if LTL_FILE.exists():
            return pd.read_csv(LTL_FILE)
        return pd.DataFrame(columns=['id', 'property_id', 'ltl_formula', 'explanation', 'operators_used', 'timestamp', 'approved'])
    
    @staticmethod
    def save_ltl_formulas(formulas):
        df = pd.DataFrame(formulas)
        
        if LTL_FILE.exists():
            existing = pd.read_csv(LTL_FILE)
            df = pd.concat([existing, df], ignore_index=True)
            df = df.drop_duplicates(subset=['id'], keep='last')
        
        df.to_csv(LTL_FILE, index=False)
    
    @staticmethod
    def approve_propositions(prop_ids, approver):
        """Mark propositions as approved"""
        df = DataManager.load_propositions()
        if 'approved_by' not in df.columns:
            df['approved_by'] = ''
        df.loc[df['id'].isin(prop_ids), 'approved'] = True
        df.loc[df['id'].isin(prop_ids), 'approved_by'] = approver
        df.to_csv(PROPOSITIONS_FILE, index=False)
    
    @staticmethod
    def approve_ltl(ltl_ids, approver):
        """Mark LTL formulas as approved"""
        df = DataManager.load_ltl_formulas()
        if 'approved_by' not in df.columns:
            df['approved_by'] = ''
        df.loc[df['id'].isin(ltl_ids), 'approved'] = True
        df.loc[df['id'].isin(ltl_ids), 'approved_by'] = approver
        df.to_csv(LTL_FILE, index=False)
    
    @staticmethod
    def generate_complete_formalization():
        """Generate complete CSV with NL, AP, LTL"""
        properties = DataManager.load_properties()
        propositions = DataManager.load_propositions()
        ltl_formulas = DataManager.load_ltl_formulas()
        
        complete_data = []
        
        for _, prop in properties.iterrows():
            prop_id = prop['id']
            
            # Get propositions for this property
            prop_propositions = propositions[propositions['property_id'] == prop_id]
            ap_list = ', '.join(prop_propositions['name'].tolist()) if not prop_propositions.empty else ''
            
            # Get LTL formula for this property
            prop_ltl = ltl_formulas[ltl_formulas['property_id'] == prop_id]
            ltl_formula = prop_ltl.iloc[0]['ltl_formula'] if not prop_ltl.empty else ''
            ltl_explanation = prop_ltl.iloc[0]['explanation'] if not prop_ltl.empty else ''
            ltl_operators = prop_ltl.iloc[0]['operators_used'] if not prop_ltl.empty else ''
            ltl_approved = prop_ltl.iloc[0]['approved'] if not prop_ltl.empty else False
            
            complete_data.append({
                'property_id': prop_id,
                'rfc_number': prop['rfc'],
                'section': prop['section'],
                'property_type': prop['type'],
                'natural_language': prop['text'],
                'atomic_propositions': ap_list,
                'ltl_formula': ltl_formula,
                'ltl_explanation': ltl_explanation,
                'ltl_operators': ltl_operators,
                'approved': ltl_approved,
                'timestamp': prop['timestamp']
            })
        
        df = pd.DataFrame(complete_data)
        df.to_csv(COMPLETE_FILE, index=False)
        return df


st.set_page_config(page_title="RFC Property Extractor", layout="wide")

# Initialize
if 'parser' not in st.session_state:
    st.session_state.parser = SmartRFCParser()
    if API_KEY:
        st.session_state.processor = PropertyProcessor(API_KEY)
    st.session_state.stage = 'upload'
    st.session_state.rfc_data = None
    st.session_state.properties = None
    st.session_state.propositions = None
    st.session_state.ltl_formulas = None

# Header
st.title("📄 RFC Property Extractor & LTL Generator")
st.caption("Extract properties → Generate atomic propositions → Generate LTL formulas")

# Sidebar stats
with st.sidebar:
    st.header("📊 Statistics")
    
    props_df = DataManager.load_properties()
    propositions_df = DataManager.load_propositions()
    ltl_df = DataManager.load_ltl_formulas()
    
    st.metric("Properties", len(props_df))
    st.metric("Atomic Propositions", len(propositions_df))
    st.metric("LTL Formulas", len(ltl_df))
    st.metric("Approved LTL", len(ltl_df[ltl_df['approved'] == True]) if 'approved' in ltl_df.columns and not ltl_df.empty else 0)
    
    st.divider()
    
    st.subheader("📥 Export Data")
    
    if st.button("Generate Complete CSV"):
        with st.spinner("Generating..."):
            complete_df = DataManager.generate_complete_formalization()
        st.success(f"✅ Generated {len(complete_df)} entries")
        
        csv = complete_df.to_csv(index=False)
        st.download_button(
            "Download Complete Formalization",
            csv,
            "complete_formalization.csv",
            "text/csv"
        )


# STAGE 1: UPLOAD & PARSE

if st.session_state.stage == 'upload':
    
    st.header("Step 1: Upload RFC Document")
    
    uploaded = st.file_uploader("Choose RFC file (txt)", type=['txt'])
    
    if uploaded:
        content = uploaded.read().decode('utf-8', errors='ignore')
        
        st.info(f"📄 {len(content):,} characters (~{len(content)//3000} pages)")
        
        if st.button("🚀 Parse & Extract Properties", type="primary"):
            
            with st.spinner("Parsing RFC..."):
                rfc_data = st.session_state.parser.parse(content)
                st.session_state.rfc_data = rfc_data
            
            st.success(f"✅ Found {len(rfc_data['property_sections'])} property-rich sections")
            
            # Show what we found
            st.write(f"**RFC {rfc_data['rfc_number']}**: {rfc_data['title']}")
            
            with st.expander("Preview sections"):
                for sec in rfc_data['property_sections'][:5]:
                    st.write(f"**Section {sec['section']}**: {sec['title']} ({sec['keywords']} keywords)")
            
            # Single API call to extract ALL properties
            if not API_KEY:
                st.error("⚠️ Set ANTHROPIC_API_KEY environment variable")
            else:
                with st.spinner("Extracting properties from all sections (1 API call)..."):
                    properties = st.session_state.processor.extract_properties_batch(
                        rfc_data['property_sections'][:10],  # Top 10 sections
                        rfc_data['rfc_number']
                    )
                    st.session_state.properties = properties
                
                if properties:
                    st.success(f"✅ Extracted {len(properties)} properties!")
                    
                    # Save immediately
                    DataManager.save_properties(properties)
                    
                    # Move to next stage
                    st.session_state.stage = 'review_properties'
                    st.rerun()
                else:
                    st.warning("No properties extracted")

# STAGE 2: REVIEW PROPERTIES

elif st.session_state.stage == 'review_properties':
    
    properties = st.session_state.properties
    
    st.header(f"Step 2: Review {len(properties)} Extracted Properties")
    
    st.write("Review the properties below. Edit if needed, then proceed to extract propositions.")
    
    # Display in editable table
    df = pd.DataFrame(properties)
    edited_df = st.data_editor(
        df[['section', 'text', 'type']],
        num_rows="dynamic",
        use_container_width=True,
        height=400
    )
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("💾 Save Changes"):
            # Update properties with edits
            for i, row in edited_df.iterrows():
                properties[i]['text'] = row['text']
                properties[i]['type'] = row['type']
            
            DataManager.save_properties(properties)
            st.success("Saved!")
    
    with col2:
        if st.button("➡️ Extract Propositions", type="primary"):
            
            # Single API call for ALL properties
            with st.spinner(f"Extracting propositions for {len(properties)} properties (1 API call)..."):
                propositions = st.session_state.processor.extract_propositions_batch(properties)
                st.session_state.propositions = propositions
            
            if propositions:
                st.success(f"✅ Extracted {len(propositions)} propositions!")
                
                # Save
                DataManager.save_propositions(propositions)
                
                # Move to approval
                st.session_state.stage = 'approve_propositions'
                st.rerun()
            else:
                st.warning("No propositions extracted")

# STAGE 3: APPROVE PROPOSITIONS

elif st.session_state.stage == 'approve_propositions':
    
    propositions = st.session_state.propositions
    properties = st.session_state.properties
    
    st.header(f"Step 3: Review & Approve Atomic Propositions")
    
    st.write(f"**{len(propositions)} propositions** extracted from **{len(properties)} properties**")
    
    # Group by property
    for prop in properties:
        prop_id = prop['id']
        prop_propositions = [p for p in propositions if p['property_id'] == prop_id]
        
        if not prop_propositions:
            continue
        
        with st.expander(f"**Property**: {prop['text'][:80]}... ({len(prop_propositions)} propositions)"):
            
            st.write(f"**Full text**: {prop['text']}")
            st.write(f"**Section**: {prop['section']} | **Type**: {prop['type']}")
            
            st.divider()
            st.write("**Atomic Propositions:**")
            
            # Editable propositions
            prop_df = pd.DataFrame(prop_propositions)[['name', 'type', 'description']]
            edited_prop_df = st.data_editor(
                prop_df,
                num_rows="dynamic",
                use_container_width=True,
                key=f"edit_{prop_id}"
            )
            
            col1, col2 = st.columns([3, 1])
            
            with col1:
                approver = st.text_input("Your name", value="User", key=f"approver_{prop_id}")
            
            with col2:
                if st.button("✅ Approve", key=f"approve_{prop_id}"):
                    # Update with edits
                    for i, row in edited_prop_df.iterrows():
                        prop_propositions[i]['name'] = row['name']
                        prop_propositions[i]['type'] = row['type']
                        prop_propositions[i]['description'] = row['description']
                    
                    # Save updated propositions
                    DataManager.save_propositions(prop_propositions)
                    
                    # Mark as approved
                    prop_ids = [p['id'] for p in prop_propositions]
                    DataManager.approve_propositions(prop_ids, approver)
                    
                    st.success("✅ Approved!")
    
    st.divider()
    
    if st.button("➡️ Generate LTL Formulas", type="primary"):
        
        # Prepare data: properties with their approved propositions
        properties_with_propositions = []
        propositions_df = pd.DataFrame(propositions)
        
        for prop in properties:
            prop_propositions = propositions_df[propositions_df['property_id'] == prop['id']].to_dict('records')
            if prop_propositions:
                properties_with_propositions.append({
                    'property': prop,
                    'propositions': prop_propositions
                })
        
        # Single API call to generate ALL LTL formulas
        with st.spinner(f"Generating LTL formulas for {len(properties_with_propositions)} properties (1 API call)..."):
            ltl_formulas = st.session_state.processor.generate_ltl_batch(properties_with_propositions)
            st.session_state.ltl_formulas = ltl_formulas
        
        if ltl_formulas:
            st.success(f"✅ Generated {len(ltl_formulas)} LTL formulas!")
            
            # Save
            DataManager.save_ltl_formulas(ltl_formulas)
            
            # Move to LTL approval
            st.session_state.stage = 'approve_ltl'
            st.rerun()
        else:
            st.warning("No LTL formulas generated")

# STAGE 4: APPROVE LTL FORMULAS

elif st.session_state.stage == 'approve_ltl':
    
    ltl_formulas = st.session_state.ltl_formulas
    properties = st.session_state.properties
    propositions = st.session_state.propositions
    
    st.header(f"Step 4: Review & Approve LTL Formulas")
    
    st.write(f"**{len(ltl_formulas)} LTL formulas** generated")
    
    # Group by property
    prop_dict = {p['id']: p for p in properties}
    propositions_df = pd.DataFrame(propositions)
    
    for ltl in ltl_formulas:
        prop_id = ltl['property_id']
        
        if prop_id not in prop_dict:
            continue
        
        prop = prop_dict[prop_id]
        prop_propositions = propositions_df[propositions_df['property_id'] == prop_id]
        
        with st.expander(f"**Property**: {prop['text'][:80]}..."):
            
            col1, col2 = st.columns([2, 1])
            
            with col1:
                st.write("**Natural Language:**")
                st.info(prop['text'])
                
                st.write("**Atomic Propositions:**")
                for _, p in prop_propositions.iterrows():
                    st.code(f"{p['name']}: {p['description']}")
            
            with col2:
                st.metric("Property Type", prop['type'])
                st.metric("Section", prop['section'])
            
            st.divider()
            
            st.write("**Generated LTL Formula:**")
            
            # Editable LTL
            ltl_formula = st.text_area(
                "LTL Formula",
                value=ltl['ltl_formula'],
                key=f"ltl_{ltl['id']}",
                height=100
            )
            
            explanation = st.text_area(
                "Explanation",
                value=ltl['explanation'],
                key=f"exp_{ltl['id']}",
                height=80
            )
            
            st.caption(f"**Operators used:** {ltl['operators_used']}")
            
            col1, col2, col3 = st.columns([2, 2, 1])
            
            with col1:
                approver = st.text_input("Your name", value="User", key=f"approver_ltl_{ltl['id']}")
            
            with col2:
                if st.button("✅ Approve LTL", key=f"approve_ltl_{ltl['id']}", type="primary"):
                    # Update LTL if edited
                    ltl['ltl_formula'] = ltl_formula
                    ltl['explanation'] = explanation
                    
                    # Save
                    DataManager.save_ltl_formulas([ltl])
                    DataManager.approve_ltl([ltl['id']], approver)
                    
                    st.success("✅ LTL Approved!")
            
            with col3:
                if st.button("⏭️ Skip", key=f"skip_ltl_{ltl['id']}"):
                    st.info("Skipped")
    
    st.divider()
    
    if st.button("🏁 Finish & View Complete Data", type="primary"):
        st.session_state.stage = 'view'
        st.rerun()

# STAGE 5: VIEW COMPLETE DATA

elif st.session_state.stage == 'view':
    
    st.header("📊 Complete Formalization Results")
    
    # Generate complete CSV
    with st.spinner("Generating complete formalization..."):
        complete_df = DataManager.generate_complete_formalization()
    
    st.success(f"✅ {len(complete_df)} complete formalizations")
    
    # Display
    st.dataframe(complete_df, use_container_width=True, height=400)
    
    # Download options
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        csv = complete_df.to_csv(index=False)
        st.download_button(
            "📥 Complete CSV",
            csv,
            "complete_formalization.csv",
            "text/csv"
        )

    with col2:
        props_csv = DataManager.load_properties().to_csv(index=False)
        st.download_button(
            "📥 Properties Only",
            props_csv,
            "properties.csv",
            "text/csv"
        )

    with col3:
        props_csv = DataManager.load_propositions().to_csv(index=False)
        st.download_button(
            "📥 Propositions Only",
            props_csv,
            "propositions.csv",
            "text/csv"
        )

    with col4:
        ltl_csv = DataManager.load_ltl_formulas().to_csv(index=False)
        st.download_button(
            "📥 LTL Only",
            ltl_csv,
            "ltl_formulas.csv",
            "text/csv"
        )

    st.divider()

    # Show sample entries
    st.subheader("📋 Sample Formalization")

    if not complete_df.empty:
        sample = complete_df.iloc[0]
        
        col1, col2 = st.columns([3, 2])
        
        with col1:
            st.write("**Natural Language:**")
            st.info(sample['natural_language'])
            
            st.write("**Atomic Propositions:**")
            st.code(sample['atomic_propositions'])
            
            st.write("**LTL Formula:**")
            st.code(sample['ltl_formula'])
            
            st.write("**Explanation:**")
            st.caption(sample['ltl_explanation'])
        
        with col2:
            st.metric("RFC", sample['rfc_number'])
            st.metric("Section", sample['section'])
            st.metric("Type", sample['property_type'])
            st.metric("Operators", sample['ltl_operators'])
            st.metric("Status", "✅ Approved" if sample['approved'] else "⏳ Pending")

    st.divider()

    if st.button("🔄 Process Another RFC"):
        st.session_state.stage = 'upload'
        st.session_state.rfc_data = None
        st.session_state.properties = None
        st.session_state.propositions = None
        st.session_state.ltl_formulas = None
        st.rerun()

st.divider()
st.caption("RFC Property Extractor & LTL Generator v2.0")