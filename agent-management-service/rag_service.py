"""
RAG Service for Agent Knowledge Base Management
Handles vector embeddings, document processing, and semantic search
"""

import os
import re
import requests
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
import chromadb
from chromadb.utils import embedding_functions
from sentence_transformers import SentenceTransformer
import pypdf
import io

# Initialize ChromaDB client
CHROMA_DATA_PATH = "./chroma_data"
os.makedirs(CHROMA_DATA_PATH, exist_ok=True)

chroma_client = chromadb.PersistentClient(path=CHROMA_DATA_PATH)

# Initialize embedding function
sentence_transformer_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)

class RAGService:
    """Service for managing knowledge bases with vector embeddings"""
    
    def __init__(self):
        self.client = chroma_client
        self.embedding_function = sentence_transformer_ef
    
    def get_or_create_collection(self, agent_id: int):
        """Get or create a ChromaDB collection for an agent"""
        collection_name = f"agent_{agent_id}_kb"
        try:
            collection = self.client.get_collection(
                name=collection_name,
                embedding_function=self.embedding_function
            )
        except:
            collection = self.client.create_collection(
                name=collection_name,
                embedding_function=self.embedding_function,
                metadata={"agent_id": str(agent_id)}
            )
        return collection
    
    def delete_collection(self, agent_id: int):
        """Delete an agent's knowledge base collection"""
        collection_name = f"agent_{agent_id}_kb"
        try:
            self.client.delete_collection(name=collection_name)
            return True
        except:
            return False
    
    def chunk_text(self, text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
        """Split text into overlapping chunks for better retrieval"""
        words = text.split()
        chunks = []
        
        for i in range(0, len(words), chunk_size - overlap):
            chunk = ' '.join(words[i:i + chunk_size])
            if len(chunk.strip()) > 50:  # Only add meaningful chunks
                chunks.append(chunk.strip())
        
        return chunks
    
    def scrape_url(self, url: str) -> str:
        """Scrape and extract text content from a URL"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(url, timeout=15, headers=headers)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'lxml')
            
            # Remove script, style, and other non-content elements
            for element in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                element.decompose()
            
            # Extract text from main content areas
            main_content = soup.find('main') or soup.find('article') or soup.find('body')
            
            if main_content:
                text = main_content.get_text(separator=' ', strip=True)
            else:
                text = soup.get_text(separator=' ', strip=True)
            
            # Clean up whitespace
            text = re.sub(r'\s+', ' ', text).strip()
            
            return text
            
        except Exception as e:
            raise Exception(f"Failed to scrape URL: {str(e)}")
    
    def process_pdf(self, pdf_content: bytes) -> str:
        """Extract text from PDF file"""
        try:
            pdf_reader = pypdf.PdfReader(io.BytesIO(pdf_content))
            text_content = ""
            
            for page in pdf_reader.pages:
                text_content += page.extract_text() + "\n"
            
            return text_content.strip()
            
        except Exception as e:
            raise Exception(f"Failed to process PDF: {str(e)}")
    
    def ingest_text(self, agent_id: int, text: str, source_type: str = "text", source_url: str = None):
        """Ingest text content into the agent's knowledge base"""
        collection = self.get_or_create_collection(agent_id)
        
        # Clear existing documents
        try:
            existing_ids = collection.get()['ids']
            if existing_ids:
                collection.delete(ids=existing_ids)
        except:
            pass
        
        # Chunk the text
        chunks = self.chunk_text(text)
        
        if not chunks:
            raise ValueError("No valid text chunks to ingest")
        
        # Prepare documents for ingestion
        ids = [f"chunk_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "source_type": source_type,
                "source_url": source_url or "direct_input",
                "chunk_index": i
            }
            for i in range(len(chunks))
        ]
        
        # Add documents to collection
        collection.add(
            documents=chunks,
            metadatas=metadatas,
            ids=ids
        )
        
        return {
            "chunks_created": len(chunks),
            "collection": collection.name
        }
    
    def ingest_url(self, agent_id: int, url: str):
        """Scrape URL and ingest content"""
        text = self.scrape_url(url)
        return self.ingest_text(agent_id, text, source_type="url", source_url=url)
    
    def ingest_file(self, agent_id: int, file_content: bytes, filename: str):
        """Process file and ingest content"""
        file_extension = os.path.splitext(filename)[1].lower()
        
        if file_extension == '.pdf':
            text = self.process_pdf(file_content)
        else:
            # Assume text file
            try:
                text = file_content.decode('utf-8')
            except UnicodeDecodeError:
                raise ValueError("File must be a text file or PDF")
        
        return self.ingest_text(agent_id, text, source_type="file", source_url=filename)
    
    def search(self, agent_id: int, query: str, top_k: int = 3) -> Dict:
        """Search the knowledge base using semantic similarity"""
        try:
            collection = self.get_or_create_collection(agent_id)
            
            # Check if collection has documents
            count = collection.count()
            if count == 0:
                return {
                    "result": "No knowledge base available for this agent",
                    "sources": []
                }
            
            # Perform semantic search
            results = collection.query(
                query_texts=[query],
                n_results=min(top_k, count)
            )
            
            if not results['documents'][0]:
                return {
                    "result": f"No relevant information found for: {query}",
                    "sources": []
                }
            
            # Combine top results
            relevant_texts = results['documents'][0]
            metadatas = results['metadatas'][0]
            
            combined_result = "\n\n".join(relevant_texts)
            
            sources = [
                {
                    "text": text,
                    "source": meta.get('source_url', 'unknown'),
                    "type": meta.get('source_type', 'unknown')
                }
                for text, meta in zip(relevant_texts, metadatas)
            ]
            
            return {
                "result": combined_result,
                "sources": sources,
                "total_chunks": count
            }
            
        except Exception as e:
            return {
                "result": f"Error searching knowledge base: {str(e)}",
                "sources": []
            }

# Global RAG service instance
rag_service = RAGService()

